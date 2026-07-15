from __future__ import annotations

import logging
from typing import Any, Protocol

import docker as docker_sdk

from lookout.docker.container import Container
from lookout.docker.recreate import build_create_kwargs

logger = logging.getLogger(__name__)

_HOST_CONFIG_KWARGS = frozenset(
    {
        "mounts",
        "restart_policy",
        "cap_add",
        "cap_drop",
        "privileged",
        "volumes_from",
        "ulimits",
        "sysctls",
        "devices",
        "device_requests",
        "dns",
        "dns_search",
        "dns_opt",
        "extra_hosts",
        "tmpfs",
        "mem_limit",
        "mem_reservation",
        "mem_swappiness",
        "nano_cpus",
        "cpu_shares",
        "cpuset_cpus",
        "cpuset_mems",
        "cpu_quota",
        "cpu_period",
        "blkio_weight",
        "oom_score_adj",
        "oom_kill_disable",
        "memswap_limit",
        "pids_limit",
        "log_config",
        "security_opt",
        "group_add",
        "read_only",
        "shm_size",
        "init",
        "pid_mode",
        "ipc_mode",
        "userns_mode",
        "uts_mode",
        "cgroup_parent",
        "isolation",
        "runtime",
        "network_mode",
        "links",
    }
)


def _strip_tag(image_name: str) -> str:
    """"repo:tag" -> "repo". Only the last ':' after the last '/' is a tag
    separator, so a registry host's port (e.g. "host:5000/repo") isn't
    mistaken for one."""
    last_slash = image_name.rfind("/")
    last_colon = image_name.rfind(":")
    return image_name[:last_colon] if last_colon > last_slash else image_name


class DockerClient(Protocol):
    def list_containers(self) -> list[Container]: ...
    def pull_image(self, image: str) -> str: ...
    def get_image_id(self, image_name: str) -> str: ...
    def find_local_image_id(self, image_name: str, digest: str) -> str | None: ...
    def stop(self, container: Container, timeout: int) -> None: ...
    def rename(self, container: Container, new_name: str) -> None: ...
    def recreate(self, container: Container, new_image_id: str) -> Container: ...
    def start(self, container: Container) -> None: ...
    def remove_image(self, image_id: str) -> None: ...
    def exec_run(self, container: Container, command: list[str]) -> tuple[int, bytes]: ...


class DockerPyClient:
    """Thin wrapper over docker-py. No auto-negotiation config needed —
    the SDK negotiates the API version against the daemon by default."""

    def __init__(self, docker_host: str | None = None) -> None:
        self._client = (
            docker_sdk.DockerClient(base_url=docker_host) if docker_host else docker_sdk.from_env()
        )

    def list_containers(self) -> list[Container]:
        raw = self._client.containers.list(all=False)
        containers = []
        for c in raw:
            repo_digests = self._repo_digests(c.attrs["Image"])
            attrs = self._resolve_network_mode_container_ref(c.attrs)
            containers.append(Container.from_inspect(attrs, repo_digests=repo_digests))
        return containers

    def _resolve_network_mode_container_ref(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """If HostConfig.NetworkMode is "container:<id>", rewrite the id to
        that target container's current name.

        Docker stores this reference by id, which goes stale if the target
        container is itself recreated (new id, e.g. also updated by lookout)
        before this one gets recreated — the id-based reference would then
        point at nothing. A name-based reference survives that, since Docker
        resolves container-mode names at create time same as it does for
        --net=container:<name>. Watchtower does this same substitution for
        the same reason.
        """
        host_config = attrs.get("HostConfig") or {}
        mode = host_config.get("NetworkMode") or ""
        if not mode.startswith("container:"):
            return attrs
        target_id = mode.split(":", 1)[1]
        try:
            target_name = self._client.containers.get(target_id).name
        except docker_sdk.errors.NotFound:
            return attrs
        host_config["NetworkMode"] = f"container:{target_name}"
        return attrs

    def _repo_digests(self, image_id: str) -> list[str]:
        try:
            digests = self._client.images.get(image_id).attrs.get("RepoDigests")
        except docker_sdk.errors.NotFound:
            return []
        return list(digests) if digests else []

    def _image_config(self, image_id: str) -> dict[str, Any]:
        """Config block of the currently-running image, for
        build_create_kwargs() to subtract from the container's own (merged)
        Config -- see that function's docstring. Missing image (e.g. removed
        out from under a running container) falls back to an empty dict,
        same as "nothing to subtract" / today's behavior."""
        try:
            config = self._client.images.get(image_id).attrs.get("Config")
        except docker_sdk.errors.NotFound:
            return {}
        return dict(config) if config else {}

    def pull_image(self, image: str) -> str:
        pulled = self._client.images.pull(image)
        return str(pulled.id)

    def get_image_id(self, image_name: str) -> str:
        return str(self._client.images.get(image_name).id)

    def find_local_image_id(self, image_name: str, digest: str) -> str | None:
        """Id of whatever local image currently carries `digest` for
        `image_name`'s repository, or None if no local image has it —
        the ground-truth fallback when a container's own image has lost its
        RepoDigests (see Container.has_digest)."""
        try:
            return str(self._client.images.get(f"{_strip_tag(image_name)}@{digest}").id)
        except docker_sdk.errors.NotFound:
            return None

    def stop(self, container: Container, timeout: int) -> None:
        self._client.containers.get(container.id).stop(timeout=timeout)

    def rename(self, container: Container, new_name: str) -> None:
        self._client.containers.get(container.id).rename(new_name)

    def recreate(self, container: Container, new_image_id: str) -> Container:
        """Create *and start* the replacement for an already-stopped
        container without losing it if anything in that sequence fails:
        rename the old container aside first, and only remove it once the
        new container has been fully created, network-attached, AND
        started. If any of that raises, the new container (if one was
        created) is torn down, the old container is renamed back, and the
        exception propagates — so there's still something to retry against
        on the next poll, instead of being gone for good.

        Starting is included here rather than left to the caller
        specifically because Docker doesn't validate every HostConfig
        setting at create() time — a `--net=container:X` container whose
        target has since vanished, for example, creates successfully and
        only fails at start(), once it actually tries to join that
        namespace. Caught live: with start() left to the caller (as an
        earlier version of this method did), that failure landed after the
        old container had already been removed, permanently losing it —
        not a retryable failure like a create()-time rejection, an
        unrecoverable one.

        On rollback, the old container is also restarted, not just renamed
        back — it was stopped by the caller specifically to make way for a
        replacement that didn't pan out, so "restore the pre-update state"
        means running, not merely present. Caught live: an earlier version
        left it renamed-back but stopped, meaning a failed update was also
        unplanned downtime until the next successful poll or manual
        intervention, on top of whatever the update failure itself already
        cost.
        """
        spec = build_create_kwargs(container, new_image_id, self._image_config(container.image_id))
        temp_name = f"{container.name}-lookout-old"
        self.rename(container, temp_name)
        new_container = None
        try:
            new_container = self._create(spec.create_kwargs)
            assert new_container.id is not None

            if spec.networks:
                # Creation auto-attaches the default bridge network; swap it
                # for the real target networks so aliases can be set
                # per-network.
                self._client.networks.get("bridge").disconnect(new_container, force=True)
                for attachment in spec.networks:
                    self._client.networks.get(attachment.name).connect(
                        new_container,
                        aliases=attachment.aliases or None,
                        ipv4_address=attachment.ipv4_address,
                        ipv6_address=attachment.ipv6_address,
                        mac_address=attachment.mac_address,
                    )

            new_container.start()
        except Exception:
            if new_container is not None:
                try:
                    new_container.remove(force=True)
                except Exception:
                    logger.exception(
                        "failed to clean up half-created container %s while rolling back "
                        "recreate() of %s",
                        new_container.id,
                        container.name,
                    )
            self.rename(container, container.name)
            try:
                self._client.containers.get(container.id).start()
            except Exception:
                logger.exception(
                    "failed to restart %s after rolling back a failed recreate", container.name
                )
            raise

        self._client.containers.get(container.id).remove()

        return Container.from_inspect(self._client.containers.get(new_container.id).attrs)

    def _create(self, create_kwargs: dict[str, Any]) -> Any:
        """containers.create(), except for two cases that need the
        low-level API instead: "volumes" (legacy Binds strings) and
        "stop_timeout".

        docker-py's high-level containers.create(volumes=[...]) doesn't
        just set HostConfig.Binds from the strings — it also independently
        derives Config.Volumes from them via _host_volume_from_bind(),
        which only understands a plain "ro"/"rw" mode suffix. Given a
        compound mode like "rw,z" (an SELinux relabel), that helper falls
        through to returning the raw "dest:mode" tail verbatim, and Docker
        creates a garbage anonymous volume at that literal bogus path
        alongside the correct bind. Caught live against a real
        SELinux-enforcing daemon — HostConfig.Binds itself came out
        correct, but three spurious extra volumes appeared alongside it.
        The low-level HostConfig(binds=[...]) path passes a list of bind
        strings straight through with no such parsing, so building the
        HostConfig ourselves via the low-level API avoids the bug
        entirely.

        Separately, "stop_timeout" isn't accepted by containers.create() /
        .run() at all — docker-py's RUN_CREATE_KWARGS/RUN_HOST_CONFIG_KWARGS
        allowlists it out entirely (confirmed by reading docker-py's own
        source; it's a real gap, not a version quirk), even though the
        low-level create_container() has always accepted it directly.
        Caught live: any container recreated with a `--stop-timeout`
        carried over from its old config raised a bare TypeError before
        ever reaching the daemon. create_container() takes it as a plain
        kwarg (not part of HostConfig at all), so it's left in `kwargs`
        below rather than routed into host_config_kwargs.

        Both cases fall through to the plain high-level call when neither
        applies — this low-level path only runs when actually needed.
        """
        if "volumes" not in create_kwargs and "stop_timeout" not in create_kwargs:
            return self._client.containers.create(**create_kwargs)

        kwargs = dict(create_kwargs)
        host_config_kwargs: dict[str, Any] = {}
        if "volumes" in kwargs:
            host_config_kwargs["binds"] = kwargs.pop("volumes")
        for key in list(kwargs):
            if key in _HOST_CONFIG_KWARGS:
                host_config_kwargs[key] = kwargs.pop(key)

        ports = kwargs.pop("ports", None)
        if ports:
            host_config_kwargs["port_bindings"] = ports
            # Config.ExposedPorts needs (port, proto) tuples, same
            # derivation the high-level API itself does internally.
            kwargs["ports"] = [tuple(p.split("/", 1)) for p in sorted(ports)]

        kwargs["host_config"] = self._client.api.create_host_config(**host_config_kwargs)
        raw = self._client.api.create_container(**kwargs)
        return self._client.containers.get(raw["Id"])

    def start(self, container: Container) -> None:
        self._client.containers.get(container.id).start()

    def remove_image(self, image_id: str) -> None:
        self._client.images.remove(image_id)

    def exec_run(self, container: Container, command: list[str]) -> tuple[int, bytes]:
        # stream=False, demux=False (the defaults) always yield (int, bytes);
        # the stubs widen the type to cover exec_run's other call shapes.
        exit_code, output = self._client.containers.get(container.id).exec_run(
            command, stream=False, demux=False
        )
        assert isinstance(exit_code, int)
        assert isinstance(output, bytes)
        return exit_code, output
