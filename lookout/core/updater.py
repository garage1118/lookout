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
    3. pull (unless no-pull) each stale, non-monitor-only container's replacement image,
       before anything is stopped
    4. stop them in dependency order, running pre-update hooks (skipping any
       container whose resolved image already matches what it's running and
       has no network-mode dependency -- nothing to actually do)
    5. recreate + start in reverse order, running post-update hooks
    6. optionally clean up superseded images
    7. record results into a Session
    """
    session = Session()

    containers = docker_client.list_containers()
    targets = filter_.apply(containers, settings)

    fallback_auth = (
        RegistryAuth(
            username=settings.registry_username,
            password=(
                settings.registry_password.get_secret_value()
                if settings.registry_password
                else None
            ),
        )
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

    # Stashed per container so the later pull step (see below) authenticates
    # with the same credentials the digest check just resolved, instead of
    # silently pulling anonymously and 401ing on a private image -- caught by
    # inspection, not live: the digest check and the pull were resolving auth
    # completely independently, and only the digest check ever got it.
    resolved_auth: dict[str, RegistryAuth | None] = {}

    for container in targets:
        if is_pinned(container.image_name):
            session.skipped.append((container, "pinned"))
            continue
        if container.has_no_tagged_image_name():
            # A container started directly from an image id has no
            # registry/repository/tag for parse_image() to work with at all
            # -- skip it with a clear, quiet reason instead of letting the
            # registry lookup below fail with a fresh logged exception (and
            # traceback) every single poll forever.
            session.skipped.append((container, "no tagged image name"))
            continue
        try:
            auth = resolve_auth(
                container.image_name,
                fallback=fallback_auth,
                fallback_registry=settings.registry_host,
            )
            resolved_auth[container.name] = auth
            latest_digest = registry_client.get_latest_digest(
                container.image_name, auth, cache=registry_auth_cache
            )
            stale = _is_stale(docker_client, container, latest_digest)
        except Exception:
            # Covers both a registry-side failure (above) and a Docker-side
            # one (_is_stale's find_local_image_id fallback) -- either way
            # this container's staleness couldn't be determined, and a
            # transient Docker API hiccup on one container shouldn't abort
            # the whole run and skip every container after it.
            logger.exception("failed to check %s for updates", container.name)
            session.skipped.append((container, "check failed"))
            continue
        if stale:
            session.stale.append(container)

    _cascade_network_mode_dependents(targets, containers, session, settings)

    to_update = [
        c for c in session.stale if not (settings.monitor_only or c.is_monitor_only())
    ]

    # Resolve every container's replacement image *before* stopping anything:
    # a pull can be slow (a large image) or fail outright (registry blip,
    # auth), and both are harmless while the container is still running. An
    # earlier version pulled after the stop, which put the pull duration
    # inside the downtime window and -- worse -- permanently abandoned the
    # container when the pull failed: nothing restarted it, and
    # list_containers() only sees running containers, so every later poll
    # was blind to it (caught live). A container whose image can't be
    # resolved is recorded as failed and never stopped at all.
    new_image_ids: dict[str, str] = {}
    for container in to_update:
        no_pull = settings.no_pull or container.is_no_pull()
        try:
            new_image_ids[container.name] = (
                docker_client.get_image_id(container.image_name)
                if no_pull
                else docker_client.pull_image(
                    container.image_name, resolved_auth.get(container.name)
                )
            )
        except Exception as exc:
            logger.exception("failed to pull a new image for %s", container.name)
            session.failed.append((container, exc))

    # A container with no network-mode dependency whose resolved image is
    # already what it's running (the common --no-pull case, or the pull path
    # if nothing new landed since the last poll) has nothing to actually do —
    # stopping and restarting it every single poll forever would be pure
    # churn (and would needlessly fire pre/post-update hooks each time). Skip
    # it entirely rather than routing it through stop/start. A container that
    # *does* have a network-mode target still needs to go through the normal
    # cycle even when its own image is unchanged, since whether it truly
    # needs recreating depends on whether that target ends up recreated this
    # run — decided by the existing target_was_recreated check below, which
    # only sees containers that were actually stopped.
    noop_names = {
        c.name
        for c in to_update
        if c.name in new_image_ids
        and c.network_mode_target() is None
        and new_image_ids[c.name] == c.image_id
    }

    order = stop_order(
        [c for c in to_update if c.name in new_image_ids and c.name not in noop_names]
    )
    stopped: list[Container] = []
    for container in order:
        try:
            lifecycle.pre_update(docker_client, container)
            docker_client.stop(container, timeout=settings.stop_timeout_seconds)
            stopped.append(container)
        except Exception as exc:
            logger.exception("failed to stop %s", container.name)
            session.failed.append((container, exc))

    recreated_names: set[str] = set()
    for container in reversed(stopped):
        try:
            new_image_id = new_image_ids[container.name]
            # Dependencies are always processed before their network-mode
            # dependents in this loop (reversed(stopped) starts them first,
            # same as any other dependency), so by the time a dependent is
            # reached here, its target's outcome this run is already known.
            target_name = container.network_mode_target()
            target_was_recreated = target_name is not None and target_name in recreated_names
            if new_image_id == container.image_id and not target_was_recreated:
                # Nothing to actually update onto. A container with no
                # network-mode dependency in this situation was already
                # filtered out before the stop loop above (see noop_names)
                # and never reaches this point at all -- this branch is only
                # ever hit by a network-mode dependent that got pulled into
                # the stop/start cycle by the cascade (see
                # _cascade_network_mode_dependents) but whose own target
                # turned out not to actually get recreated this run after
                # all. Restart it in place instead and leave it counted as
                # stale-not-updated.
                # A network-mode dependent whose target *was* actually
                # recreated this run skips this shortcut even though its own
                # image is unchanged — see _cascade_network_mode_dependents.
                docker_client.start(container)
                continue
            new_container = docker_client.recreate(container)
            recreated_names.add(container.name)
            # recreate() creates, network-attaches, and starts the
            # replacement as one atomic-ish unit (rolling itself back on any
            # failure in that sequence), so the container is already running
            # by this point — no separate start() call needed. A post-update
            # hook that errors out (as opposed to one that runs and exits
            # non-zero, which _run_hook only warns about) shouldn't turn a
            # successful update into a "failed" one.
            try:
                lifecycle.post_update(docker_client, new_container)
            except Exception:
                logger.exception("post-update hook errored on %s", new_container.name)
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


def _cascade_network_mode_dependents(
    targets: list[Container],
    all_containers: list[Container],
    session: Session,
    settings: Settings,
) -> None:
    """Mark as stale any container sharing its network namespace with
    something already marked stale this run, even though its own image is
    unchanged, so it gets a real recreate (not just a same-image restart —
    core/updater.run's restart-vs-recreate shortcut separately checks
    whether the target actually ended up recreated this run before deciding
    to skip it).

    Docker resolves a `container:<name>` reference to a concrete id at
    create() time and never updates the stored value again — so once a
    network-mode target is recreated (new id) in one poll, a dependent left
    untouched keeps referencing the target's now-dead id, and its own next
    recreate fails at start() with "No such container", permanently (every
    later poll hits the same dead reference). Recreating the dependent in
    the *same* poll as its target sidesteps this entirely: Container.links()
    (via network_mode_target()) already makes stop_order() recreate the
    target first, and build_create_kwargs() re-resolves the dependent's
    reference to a name while the target's old id still exists this run —
    Docker then re-resolves that name to the target's *new* id at the
    dependent's own create() time.

    A target that's monitor-only (globally or via its own label) is never
    cascaded from: it structurally can never actually be recreated by
    lookout while that holds, so there's nothing to protect a dependent
    against, ever — cascading anyway would just stop/start the dependent on
    every single poll for as long as the target stays monitor-only-stale,
    for no benefit.

    `all_containers` is the *pre-filter* list (before core/filter.apply()),
    used only to warn about a network-mode dependent this cascade can't
    reach at all because --include/--exclude/--label-enable filtered it out
    of `targets` entirely: the same dead-reference gap this whole mechanism
    exists to close, but with no fix available short of the operator
    adjusting their filters, since a container outside the monitored set is
    never stopped/recreated/started by lookout in the first place.
    """
    stale_names = {c.name for c in session.stale}
    by_name = {c.name: c for c in targets}
    changed = True
    while changed:
        changed = False
        for container in targets:
            if container.name in stale_names:
                continue
            target_name = container.network_mode_target()
            if target_name not in stale_names:
                continue
            target = by_name.get(target_name)
            if target is not None and (settings.monitor_only or target.is_monitor_only()):
                continue
            session.stale.append(container)
            stale_names.add(container.name)
            changed = True

    for container in all_containers:
        if container.name in by_name:
            continue  # in scope -- already handled (or ruled out) above
        target_name = container.network_mode_target()
        if target_name not in stale_names:
            continue
        target = by_name.get(target_name)
        if target is not None and (settings.monitor_only or target.is_monitor_only()):
            continue
        logger.warning(
            "%s shares its network namespace with %s, which is stale and being recreated "
            "this run, but %s is filtered out of lookout's monitored set -- its network_mode "
            "reference will go stale once %s is recreated, since lookout never stops/starts "
            "containers outside the monitored set",
            container.name,
            target_name,
            container.name,
            target_name,
        )


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
        except Exception as exc:
            logger.debug("skipping cleanup of %s: %s", old.image_id, exc)
