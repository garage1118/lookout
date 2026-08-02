from __future__ import annotations

import logging

import apprise

from lookout import __version__
from lookout.core.session import Session

logger = logging.getLogger(__name__)


def _deliver(notification_urls: list[str], body: str, title: str) -> None:
    if not notification_urls:
        return

    apobj = apprise.Apprise()
    for url in notification_urls:
        if not apobj.add(url):
            logger.warning("failed to parse notification URL: %s", url)

    if not apobj.notify(body=body, title=title):
        logger.warning("one or more notifications failed to send")


def _title(base: str, scope: str | None) -> str:
    """Distinguishes which instance a notification came from when several
    scoped lookouts share the same notification target (e.g. one Telegram
    bot) -- otherwise indistinguishable run summaries from different
    instances would arrive with no way to tell them apart."""
    return f"{base} [{scope}]" if scope else base


def send(
    session: Session,
    notification_urls: list[str],
    only_on_change: bool = False,
    scope: str | None = None,
) -> None:
    if only_on_change and not session.has_activity():
        return
    _deliver(notification_urls, session.summary(), _title("lookout run summary", scope))


def send_startup(notification_urls: list[str], scope: str | None = None) -> None:
    _deliver(notification_urls, f"lookout v{__version__} started", _title("lookout started", scope))
