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


def send(session: Session, notification_urls: list[str], only_on_change: bool = False) -> None:
    if only_on_change and not session.has_activity():
        return
    _deliver(notification_urls, session.summary(), "lookout run summary")


def send_startup(notification_urls: list[str]) -> None:
    _deliver(notification_urls, f"lookout v{__version__} started", "lookout started")
