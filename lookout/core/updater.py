from __future__ import annotations

import logging

from lookout.config import Settings
from lookout.core import filter as filter_
from lookout.core import lifecycle
from lookout.core.session import Session
from lookout.docker.client import DockerClient
from lookout.docker.container import Container
from lookout.registry.auth import RegistryAuth, resolve_auth
from lookout.registry.digest import AuthCache, RegistryClient, is_pinned

logger = logging.getLogger(__name__)


def run(
    docker_client: DockerClient, registry_client: RegistryClient, settings: Settings
) -> Session:
    """
    1. list + filter containers
    2. check staleness against the registry (pinned images are skipped)
    3. stop stale, non-monitor-only containers in dependency order, running pre-update hooks
    4. pull (unless no-pull), recreate, start in reverse order, running post-update hooks
    5. optionally clean up superseded images
    6. record results into a Session
    """
    session = Session()

    containers = docker_client.list_containers()
    targets = filter_.apply(containers, settings)

    fallback_auth = (
        RegistryAuth(username=settings.registry_username, password=settings.registry_password)
        if settings.registry_username and settings.registry_host
        else None
    )
    if settings.registry_username and not settings.registry_host:
        logger.warning(
            "LOOKOUT_REGISTRY_USERNAME is set but LOOKOUT_REGISTRY_HOST is not — "
            "the fallback credentials will not be used for anything"
        )

    # Fresh per run, not shared across polls: each registry's auth challenge
    # (anonymous / bearer-realm / basic) is probed once here no matter how
    # many images on it are checked, instead of once per image.
    registry_auth_cache: AuthCache = {}

    for container in targets:
        if is_pinned(container.image_name):
            session.skipped.append(container)
            continue
        try:
            auth = resolve_auth(
                container.image_name,
                fallback=fallback_auth,
                fallback_registry=settings.registry_host,
            )
            latest_digest = registry_client.get_latest_digest(
                container.image_name, auth, cache=registry_auth_cache
            )
        except Exception:
            logger.exception("failed to check %s for updates", container.name)
            session.skipped.append(container)
            continue
        if _is_stale(docker_client, container, latest_digest):
            session.stale.append(container)

    to_update = [
        c for c in session.stale if not (settings.monitor_only or c.is_monitor_only())
    ]

    order = stop_order(to_update)
    stopped: list[Container] = []
    for container in order:
        try:
            lifecycle.pre_update(docker_client, container)
            docker_client.stop(container, timeout=settings.stop_timeout_seconds)
            stopped.append(container)
        except Exception as exc:
            logger.exception("failed to stop %s", container.name)
            session.failed.append((container, exc))

    for container in reversed(stopped):
        try:
            no_pull = settings.no_pull or container.is_no_pull()
            new_image_id = (
                docker_client.get_image_id(container.image_name)
                if no_pull
                else docker_client.pull_image(container.image_name)
            )
            new_container = docker_client.recreate(container, new_image_id)
            docker_client.start(new_container)
            lifecycle.post_update(docker_client, new_container)
            session.updated.append(new_container)
        except Exception as exc:
            logger.exception("failed to update %s", container.name)
            session.failed.append((container, exc))

    if settings.cleanup:
        _cleanup_images(docker_client, stopped, session.updated)

    return session


def _is_stale(docker_client: DockerClient, container: Container, latest_digest: str) -> bool:
    if container.has_digest(latest_digest):
        return False
    # The container's own image may genuinely be behind, or it may just have
    # lost its RepoDigests because its tag got locally reassigned out from
    # under it (see Container.has_digest). Either way, ask Docker directly
    # whether the *running* image is the one that actually has this digest.
    found_id = docker_client.find_local_image_id(container.image_name, latest_digest)
    return found_id is None or found_id != container.image_id


def stop_order(containers: list[Container]) -> list[Container]:
    """Dependents before dependencies, so `reversed(stop_order(...))` starts
    dependencies first — matching Watchtower's link/depends-on ordering."""
    return list(reversed(_dependency_first_order(containers)))


def _dependency_first_order(containers: list[Container]) -> list[Container]:
    by_name = {c.name: c for c in containers}
    visited: set[str] = set()
    result: list[Container] = []

    def visit(c: Container) -> None:
        if c.name in visited:
            return
        visited.add(c.name)
        for dep_name in c.links():
            dep = by_name.get(dep_name)
            if dep is not None:
                visit(dep)
        result.append(c)

    for c in containers:
        visit(c)
    return result


def _cleanup_images(
    docker_client: DockerClient, stopped: list[Container], updated: list[Container]
) -> None:
    """Best-effort removal of images superseded by a successful update.

    Relies on Docker's own "image in use" check as the safety net for the
    known ordering hazard (don't remove an image another container still
    references) rather than tracking references ourselves.
    """
    updated_names = {c.name for c in updated}
    for old in stopped:
        if old.name not in updated_names:
            continue  # update failed; old image is still the running one
        try:
            docker_client.remove_image(old.image_id)
        except Exception:
            logger.debug("skipping cleanup of %s (still in use?)", old.image_id)
