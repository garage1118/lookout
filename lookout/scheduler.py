from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable
from types import FrameType

logger = logging.getLogger(__name__)


def run_forever(job: Callable[[], None], interval_seconds: int) -> None:
    """Simple sleep-loop scheduler. Cron-style scheduling can replace this
    later (see open question in the handoff doc) without touching `job`.

    Exits promptly on SIGTERM/SIGINT rather than waiting out the interval —
    lookout typically runs as a container itself, and `docker stop` sends
    SIGTERM then escalates to SIGKILL after a grace period. A run already in
    progress is allowed to finish rather than being cancelled mid-recreate.
    """
    stop_requested = False

    def handle_signal(signum: int, frame: FrameType | None) -> None:
        nonlocal stop_requested
        logger.info("received signal %d, shutting down after this run", signum)
        stop_requested = True

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    while not stop_requested:
        try:
            job()
        except Exception:
            logger.exception("run failed")

        for _ in range(interval_seconds):
            if stop_requested:
                break
            time.sleep(1)
