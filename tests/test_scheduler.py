from __future__ import annotations

import os
import signal

from lookout.scheduler import run_forever


def test_run_forever_calls_job_until_signaled() -> None:
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        if len(calls) == 3:
            os.kill(os.getpid(), signal.SIGTERM)

    run_forever(job, interval_seconds=0)

    assert len(calls) == 3


def test_run_forever_survives_job_exceptions() -> None:
    calls: list[int] = []

    def job() -> None:
        calls.append(1)
        if len(calls) == 2:
            os.kill(os.getpid(), signal.SIGTERM)
        raise RuntimeError("boom")

    run_forever(job, interval_seconds=0)

    assert len(calls) == 2
