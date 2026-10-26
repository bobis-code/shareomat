# -*- coding: utf-8 -*-
"""The cron scheduler must actually fire, in Swiss local time (regression: it never fired)."""

import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("croniter")

from shareomat.core.collector import leg_scheduler  # noqa: E402
from shareomat.core.collector.leg_scheduler import SchedulerThread, next_run_after  # noqa: E402

ZURICH = ZoneInfo("Europe/Zurich")


def test_next_run_is_local_time_in_winter_and_summer():
    winter = next_run_after("0 6 * * *", datetime(2026, 11, 1, 6, 0, tzinfo=ZURICH))
    assert winter == datetime(2026, 11, 2, 6, 0, tzinfo=ZURICH)  # strictly after, local 06:00
    summer = next_run_after("0 6 * * *", datetime(2027, 7, 1, 5, 0, tzinfo=ZURICH))
    assert summer.hour == 6 and summer.utcoffset().total_seconds() == 7200


def test_next_run_across_dst_change_keeps_local_hour():
    # 25.10.2026: switch back to winter time; next 06:00 after Saturday 06:00 is Sunday 06:00 local.
    nxt = next_run_after("0 6 * * *", datetime(2026, 10, 24, 6, 0, tzinfo=ZURICH))
    assert (nxt.day, nxt.hour) == (25, 6)


def test_scheduler_fires_when_due(monkeypatch):
    fired = threading.Event()
    start = datetime.now(ZURICH)
    calls = iter([start.replace(microsecond=0)])

    def fake_next(expr, after):
        try:
            return next(calls)          # first: due right now
        except StopIteration:
            return after.replace(year=after.year + 1)  # then far away

    monkeypatch.setattr(leg_scheduler, "next_run_after", fake_next)
    thread = SchedulerThread("* * * * *", fired.set)
    thread.start()
    try:
        assert fired.wait(timeout=5), "scheduler never fired"
    finally:
        thread.stop()
        thread.join(timeout=5)
