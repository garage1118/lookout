from __future__ import annotations

from typing import Any, Protocol

import docker as docker_sdk

from lookout.docker.container import Container
from lookout.docker.recreate import build_create_kwargs


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
        """Create the replacement for an already-stopped container without
        losing it if creation fails: rename the old container aside first,
        and only remove it once the new container has been created. If
        create() raises (kwargs Docker rejects, a network that disappeared,
        a daemon hiccup), the old container is renamed back so there's still
        something to retry against on the next poll, instead of being gone
        for good.

        Does not start the new container — the caller starts it explicitly so
        post-update lifecycle hooks can run against a known-running container.
        """
        spec = build_create_kwargs(container, new_image_id)
        temp_name = f"{container.name}-lookout-old"
        self.rename(container, temp_name)
        try:
            new_container = self._client.containers.create(**spec.create_kwargs)
        except Exception:
            self.rename(container, container.name)
            raise
        assert new_container.id is not None

        if spec.networks:
            # Creation auto-attaches the default bridge network; swap it for
            # the real target networks so aliases can be set per-network.
            self._client.networks.get("bridge").disconnect(new_container, force=True)
            for attachment in spec.networks:
                self._client.networks.get(attachment.name).connect(
                    new_container,
                    aliases=attachment.aliases or None,
                    ipv4_address=attachment.ipv4_address,
                    ipv6_address=attachment.ipv6_address,
                    mac_address=attachment.mac_address,
                )

        self._client.containers.get(container.id).remove()

        return Container.from_inspect(self._client.containers.get(new_container.id).attrs)

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
