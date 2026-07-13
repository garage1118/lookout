from __future__ import annotations

import logging

import apprise

from lookout.core.session import Session

logger = logging.getLogger(__name__)


def send(session: Session, notification_urls: list[str]) -> None:
    if not notification_urls:
        return

    apobj = apprise.Apprise()
    for url in notification_urls:
        if not apobj.add(url):
            logger.warning("failed to parse notification URL: %s", url)

    if not apobj.notify(body=session.summary(), title="lookout run summary"):
        logger.warning("one or more notifications failed to send")
