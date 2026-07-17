from __future__ import annotations

import logging
from typing import Any, Protocol

import docker as docker_sdk

from lookout.docker.container import Container
from lookout.docker.recreate import build_create_kwargs
from lookout.registry.auth import RegistryAuth

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
    def is_swarm_active(self) -> bool: ...
    def list_containers(self) -> list[Container]: ...
    def pull_image(self, image: str, auth: RegistryAuth | None = None) -> str: ...
    def get_image_id(self, image_name: str) -> str: ...
    def find_local_image_id(self, image_name: str, digest: str) -> str | None: ...
    def stop(self, container: Container, timeout: int) -> None: ...
    def rename(self, container: Container, new_name: str) -> None: ...
    def recreate(self, container: Container) -> Container: ...
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

    def is_swarm_active(self) -> bool:
        """True if this daemon is a member of a Swarm (manager or worker).

        Watchtower users hit a real trap here: running against a
        Swarm-enabled daemon produced a clean, error-free poll that updated
        nothing at all, for weeks, before anyone traced it back to Swarm --
        service-managed containers get task-suffixed names
        (`<service>.<slot>.<task-id>`) that never match a plain `--include`
        entry, and even a container an operator did manage to select would
        just get overwritten again by Swarm's own reconciliation the moment
        lookout recreated it out from under the service. cli.py logs an
        explicit warning based on this at startup instead of letting that
        same silent no-op recur here."""
        state = (self._client.info().get("Swarm") or {}).get("LocalNodeState")
        return state not in (None, "", "inactive")

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

    def pull_image(self, image: str, auth: RegistryAuth | None = None) -> str:
        """`auth` is the same credential the registry digest check just
        resolved for this image (config.json, or the LOOKOUT_REGISTRY_*
        fallback) -- without forwarding it here, docker-py falls back to
        whatever `config.json` lookout's own container happens to have (often
        none at all, e.g. a Portainer-style deployment using only the
        env-var fallback), so the digest check would authenticate correctly
        while the pull itself 401s anonymously."""
        auth_config = (
            {"username": auth.username, "password": auth.password or ""}
            if auth and auth.username
            else None
        )
        pulled = self._client.images.pull(image, auth_config=auth_config)
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

    def recreate(self, container: Container) -> Container:
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

        A `<name>-lookout-old` container can be left behind by a *previous*
        recreate() that never reached its own final removal step (process
        killed between the rename and the remove, or that remove() itself
        failing after a successful start — see below): list_containers()
        only sees running containers, so such an orphan is invisible to
        lookout's normal polling, but it still occupies the temp name below
        -- without clearing it first, the rename would hit a 409 name
        conflict and this container's updates would fail identically,
        forever, until someone removed the orphan by hand.
        """
        spec = build_create_kwargs(container, self._image_config(container.image_id))
        create_kwargs = spec.create_kwargs
        if spec.networks:
            # Every target network is attached directly at create() time
            # with its own full endpoint config (aliases/static IP/MAC) in
            # the same call, exactly like a plain `docker run --network X
            # --ip ...` (plus `docker network connect` for any others, done
            # atomically here instead of as separate calls afterward) --
            # rather than creating on the default bridge network and
            # disconnecting/reconnecting onto the real ones after. That
            # bridge detour left HostConfig.NetworkMode permanently
            # reporting "bridge" even though the container was correctly
            # attached elsewhere (Docker never rewrites NetworkMode after
            # creation), which turned out to be more than cosmetic: caught
            # live via a real Portainer report -- Portainer's own
            # "Duplicate/Edit" form pre-fills its network selector from
            # NetworkMode, not the container's actual
            # NetworkSettings.Networks, so re-deploying a lookout-recreated
            # container from Portainer silently dropped it onto bridge (and
            # re-attached the real network with no static IP config,
            # landing a different auto-assigned address). An earlier
            # version of this fix only covered a single target network,
            # under the assumption that a single create() call couldn't
            # reliably attach more than one fully-configured network at
            # once -- that assumption did not hold up under live testing
            # against a real modern daemon (multiple full EndpointsConfig
            # entries in one create_container() call worked cleanly), so
            # the bridge-then-swap path this comment used to describe has
            # been retired entirely rather than kept around for the
            # multi-network case alone. `network_mode` is set to the first
            # target network arbitrarily -- Docker has no strong concept of
            # "primary" once a container has more than one attachment, and
            # `NetworkMode` naming one over another doesn't change which
            # networks are actually attached or how they behave.
            create_kwargs = dict(create_kwargs)
            create_kwargs["network_mode"] = spec.networks[0].name
            create_kwargs["networking_config"] = self._client.api.create_networking_config(
                {
                    attachment.name: self._client.api.create_endpoint_config(
                        aliases=attachment.aliases or None,
                        ipv4_address=attachment.ipv4_address,
                        ipv6_address=attachment.ipv6_address,
                        mac_address=attachment.mac_address,
                    )
                    for attachment in spec.networks
                }
            )

        temp_name = f"{container.name}-lookout-old"
        self._remove_stale_temp_container(temp_name)
        self.rename(container, temp_name)
        new_container = None
        try:
            new_container = self._create(create_kwargs)
            assert new_container.id is not None
            new_container.start()
        except Exception:
            # Every step below is best-effort and individually guarded: this
            # block's job is to restore as much of the pre-update state as
            # it can without ever letting a failure *here* replace the
            # original exception being rolled back from (a bare `raise` at
            # the end always re-raises that original exception, regardless
            # of what happened in between) or short-circuit a later
            # best-effort step.
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
            try:
                self.rename(container, container.name)
            except Exception:
                # Left as <name>-lookout-old; _remove_stale_temp_container()
                # clears it out of the way before this container's next
                # recreate() attempt.
                logger.exception(
                    "failed to rename %s back to its original name during recreate "
                    "rollback -- it will remain as %s until the next recreate",
                    container.name,
                    temp_name,
                )
            try:
                self._client.containers.get(container.id).start()
            except Exception:
                logger.exception(
                    "failed to restart %s after rolling back a failed recreate", container.name
                )
            raise

        try:
            self._client.containers.get(container.id).remove()
        except Exception:
            # The update itself already succeeded (new_container is created,
            # network-attached, and running) -- don't let a cleanup failure
            # here turn that into a reported failure. Left behind as
            # <name>-lookout-old; cleaned up automatically by
            # _remove_stale_temp_container() on this container's next
            # recreate(), whenever that next happens.
            logger.exception(
                "recreate of %s succeeded but removing the old container (renamed to %s) failed",
                container.name,
                temp_name,
            )

        return Container.from_inspect(self._client.containers.get(new_container.id).attrs)

    def _remove_stale_temp_container(self, temp_name: str) -> None:
        """Best-effort removal of a `<name>-lookout-old` container left over
        from a previous recreate() that didn't reach its own cleanup step —
        see recreate()'s docstring. Not found is the overwhelmingly common
        case (no prior crash) and is silently fine; any other failure is
        logged but not fatal, since the rename right after this will simply
        fail loudly (and retryably) on its own if the conflict is still
        there."""
        try:
            self._client.containers.get(temp_name).remove(force=True)
        except docker_sdk.errors.NotFound:
            pass
        except Exception:
            logger.exception(
                "failed to remove stale leftover container %s before recreate", temp_name
            )

    def _create(self, create_kwargs: dict[str, Any]) -> Any:
        """containers.create(), except for three cases that need the
        low-level API instead: "volumes" (legacy Binds strings),
        "stop_timeout", and "networking_config".

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

        "networking_config" (a single target network's full endpoint config
        -- aliases/static IP/MAC -- attached directly at create() time, see
        recreate()) similarly isn't part of RUN_CREATE_KWARGS/
        RUN_HOST_CONFIG_KWARGS on the high-level API, even though the
        low-level create_container() has always accepted it directly as a
        top-level kwarg (not part of HostConfig at all, so — like
        stop_timeout — it's left in `kwargs` below rather than routed into
        host_config_kwargs).

        All three cases fall through to the plain high-level call when none
        applies — this low-level path only runs when actually needed.
        """
        if (
            "volumes" not in create_kwargs
            and "stop_timeout" not in create_kwargs
            and "networking_config" not in create_kwargs
        ):
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
