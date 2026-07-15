from __future__ import annotations

import logging
import time

import click

from lookout import __version__
from lookout.config import Settings
from lookout.core.updater import run as run_update
from lookout.docker.client import DockerPyClient
from lookout.notifications.notify import send as send_notifications
from lookout.notifications.notify import send_startup
from lookout.registry.digest import RegistryClient
from lookout.scheduler import run_forever

logger = logging.getLogger(__name__)


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--interval", type=int, default=None, help="Poll interval in seconds")
@click.option("--run-once", is_flag=True, help="Run a single pass and exit")
@click.option(
    "--include",
    "include_names",
    multiple=True,
    help="Only monitor this container name (repeatable)",
)
@click.option(
    "--exclude",
    "exclude_names",
    multiple=True,
    help="Never monitor this container name (repeatable)",
)
@click.option(
    "--label-enable",
    is_flag=True,
    default=None,
    help="Only monitor containers with the enable label set to true",
)
@click.option(
    "--cleanup", is_flag=True, default=None, help="Remove dangling images after a successful update"
)
@click.option(
    "--monitor-only", is_flag=True, default=None, help="Report staleness but never update"
)
@click.option(
    "--no-pull",
    is_flag=True,
    default=None,
    help="Never pull; only recreate from images already present",
)
@click.option("--docker-host", default=None, help="Docker daemon URL (defaults to the SDK's own)")
@click.option("--log-level", default=None, help="Python logging level, e.g. DEBUG, INFO, WARNING")
@click.option(
    "--notify-only-on-change",
    is_flag=True,
    default=None,
    help="Skip sending a notification when nothing was updated, failed, or found stale",
)
@click.option(
    "--notify-on-startup",
    is_flag=True,
    default=None,
    help="Send a one-time notification when lookout starts",
)
def main(
    interval: int | None,
    run_once: bool,
    include_names: tuple[str, ...],
    exclude_names: tuple[str, ...],
    label_enable: bool | None,
    cleanup: bool | None,
    monitor_only: bool | None,
    no_pull: bool | None,
    docker_host: str | None,
    log_level: str | None,
    notify_only_on_change: bool | None,
    notify_on_startup: bool | None,
) -> None:
    settings = Settings()

    if interval is not None:
        settings.interval_seconds = interval
    if include_names:
        settings.include_names = list(include_names)
    if exclude_names:
        settings.exclude_names = list(exclude_names)
    if label_enable is not None:
        settings.label_enable = label_enable
    if cleanup is not None:
        settings.cleanup = cleanup
    if monitor_only is not None:
        settings.monitor_only = monitor_only
    if no_pull is not None:
        settings.no_pull = no_pull
    if docker_host is not None:
        settings.docker_host = docker_host
    if log_level is not None:
        settings.log_level = log_level
    if notify_only_on_change is not None:
        settings.notify_only_on_change = notify_only_on_change
    if notify_on_startup is not None:
        settings.notify_on_startup = notify_on_startup

    logging.Formatter.converter = time.gmtime  # timestamps in UTC regardless of container TZ
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%SZ",
    )
    # httpx logs one line per HTTP request at INFO, which drowns out lookout's own
    # INFO-level summaries with registry request noise. Keep it available for
    # LOOKOUT_LOG_LEVEL=DEBUG troubleshooting, quiet otherwise.
    if settings.log_level.upper() != "DEBUG":
        logging.getLogger("httpx").setLevel(logging.WARNING)

    logger.info("lookout v%s started", __version__)

    if settings.notify_on_startup:
        send_startup(settings.notification_urls)

    docker_client = DockerPyClient(docker_host=settings.docker_host)
    registry_client = RegistryClient()

    def job() -> None:
        session = run_update(docker_client, registry_client, settings)
        logger.info(session.summary())
        send_notifications(session, settings.notification_urls, settings.notify_only_on_change)

    if run_once:
        job()
    else:
        run_forever(job, settings.interval_seconds)


if __name__ == "__main__":
    main()
