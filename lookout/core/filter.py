from __future__ import annotations

import os
import re
from pathlib import Path

from lookout.config import Settings
from lookout.docker.container import ENABLE_LABEL, Container

_MOUNTINFO_CONTAINER_ID = re.compile(r"containers/([0-9a-f]{64})/hostname /etc/hostname\b")


def apply(
    containers: list[Container],
    settings: Settings,
    own_container_id: str | None = None,
) -> list[Container]:
    """Include/exclude by name, enable-label scope, and disabled-via-label.

    Order: self-exemption (absolute, see below), then disabled-via-label,
    then --label-enable scope (bypassed by an explicit --include, see
    below), then name include list, then name exclude list (exclude always
    wins last).
    """
    if own_container_id is None:
        own_container_id = _detect_own_container_id()

    result = []
    for container in containers:
        if own_container_id and container.id.startswith(own_container_id):
            # Never let lookout target its own container, deliberately not
            # overridable via --include: stopping itself to recreate itself
            # could leave nothing running to complete or retry the update.
            continue
        if not container.is_monitored():
            continue
        explicitly_included = container.name in settings.include_names
        if settings.label_enable and not explicitly_included and (
            container.labels.get(ENABLE_LABEL, "").lower() != "true"
        ):
            # Some deployment tools (Portainer stacks in particular) make it
            # impractical to attach a custom label to a container at all --
            # under --label-enable scope such a container could otherwise
            # never be reached, --include or not, since it can never satisfy
            # the "has the label" requirement. An explicit --include name is
            # a stronger, more deliberate signal than the label scope it's
            # bypassing, so it wins here -- but this only widens *scope*: a
            # container explicitly disabled via io.lookout.enable=false was
            # already filtered out above (is_monitored()) and --include
            # doesn't reach it either, same as monitor-only/no-pull below
            # this function stay in full effect regardless of how a
            # container entered scope.
            continue
        if settings.include_names and not explicitly_included:
            continue
        if container.name in settings.exclude_names:
            continue
        result.append(container)
    return result


def _detect_own_container_id() -> str | None:
    """Best-effort id of the container lookout itself is running in.

    Reads /proc/self/mountinfo for /etc/hostname's bind-mount source, which
    Docker always sets to /var/lib/docker/containers/<real-id>/hostname on
    the host — unlike $HOSTNAME, this isn't affected by a --hostname
    override (confirmed live: a container deployed with an explicit,
    unrelated hostname still resolves correctly here). Falls back to
    $HOSTNAME (Docker's default, unoverridden hostname) if /proc isn't
    available at all, e.g. not running on Linux.
    """
    try:
        mountinfo = Path("/proc/self/mountinfo").read_text()
    except OSError:
        return os.environ.get("HOSTNAME")
    return _parse_own_container_id(mountinfo) or os.environ.get("HOSTNAME")


def _parse_own_container_id(mountinfo: str) -> str | None:
    match = _MOUNTINFO_CONTAINER_ID.search(mountinfo)
    return match.group(1) if match else None
