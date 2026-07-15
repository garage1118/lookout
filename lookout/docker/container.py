from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

ENABLE_LABEL = "io.lookout.enable"
MONITOR_ONLY_LABEL = "io.lookout.monitor-only"
NO_PULL_LABEL = "io.lookout.no-pull"
DEPENDS_ON_LABEL = "io.lookout.depends-on"


@dataclass
class Container:
    """Domain model wrapping a raw `docker inspect` payload."""

    id: str
    name: str
    image_id: str
    image_name: str
    labels: dict[str, str]
    inspect: dict[str, Any]
    repo_digests: list[str] = field(default_factory=list)
    """RepoDigests of the currently-running image, e.g. "alpine@sha256:...".
    Populated separately from `inspect` since it comes from an image lookup,
    not the container's own inspect payload. Empty for locally-built images
    that were never pulled from a registry."""

    @classmethod
    def from_inspect(cls, data: dict[str, Any], repo_digests: list[str] | None = None) -> Container:
        config = data.get("Config", {})
        return cls(
            id=data["Id"],
            name=data["Name"].lstrip("/"),
            image_id=data["Image"],
            image_name=config.get("Image", ""),
            labels=config.get("Labels") or {},
            inspect=data,
            repo_digests=repo_digests or [],
        )

    def is_monitored(self) -> bool:
        """Not explicitly disabled via the enable label.

        Scope (--label-enable, requiring the label to be "true") and name
        include/exclude are applied separately in core/filter.py, which has
        access to Settings; this only covers the disable-via-label case.
        """
        return self.labels.get(ENABLE_LABEL, "true").lower() != "false"

    def is_monitor_only(self) -> bool:
        return self.labels.get(MONITOR_ONLY_LABEL, "false").lower() == "true"

    def is_no_pull(self) -> bool:
        return self.labels.get(NO_PULL_LABEL, "false").lower() == "true"

    def links(self) -> list[str]:
        """Names of containers this one depends on (legacy links, depends-on
        label, and network_mode_target — a container sharing another's
        network namespace can't start before that other container exists)."""
        names: set[str] = set()
        for link in (self.inspect.get("HostConfig") or {}).get("Links") or []:
            # "/other-container:/this-container/alias" -> "other-container"
            names.add(link.split(":", 1)[0].lstrip("/"))
        depends_on = self.labels.get(DEPENDS_ON_LABEL, "")
        names.update(name.strip() for name in depends_on.split(",") if name.strip())
        network_target = self.network_mode_target()
        if network_target:
            names.add(network_target)
        return sorted(names)

    def network_mode_target(self) -> str | None:
        """Name of the container this one shares its network namespace with
        (`--net=container:<name-or-id>`), or None if it isn't sharing
        another container's namespace.

        Relies on DockerPyClient._resolve_network_mode_container_ref having
        already rewritten an id-based reference to a name at listing time —
        this only sees a name here, never a raw id.
        """
        network_mode = (self.inspect.get("HostConfig") or {}).get("NetworkMode") or ""
        if not network_mode.startswith("container:"):
            return None
        return network_mode.split(":", 1)[1]

    def has_digest(self, digest: str) -> bool:
        """True if `digest` is among this image's known RepoDigests.

        A cheap fast-path check only — False does *not* mean the container is
        stale. Docker clears an image's RepoDigests whenever its tag gets
        locally reassigned to a different image (e.g. a `docker build -t` or
        `docker pull` of the same tag by something other than lookout), which
        orphans this metadata on the image the container is still actually
        running. core/updater.py falls back to an image-id comparison via
        DockerClient.find_local_image_id() when this returns False, rather
        than treating an empty/mismatched list as proof of freshness.
        """
        local_digests = {d.rsplit("@", 1)[1] for d in self.repo_digests if "@" in d}
        return digest in local_digests
