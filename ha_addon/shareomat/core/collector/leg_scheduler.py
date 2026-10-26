# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_scheduler.py

Purpose:
    Background thread that fires a settlement run on a cron schedule.
    Uses croniter for schedule evaluation when available.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The cron expression is evaluated in LOCAL time (SHAREOMAT_TZ, default
    Europe/Zurich) — "0 6 * * *" means 06:00 in Switzerland, summer and
    winter. The thread computes the next due time with croniter.get_next()
    and sleeps until then (in short steps, so stop() stays responsive).

    The previous implementation compared croniter.get_prev(now) with now;
    get_prev() returns the PREVIOUS occurrence even at the exact minute, so
    that comparison never matched and the schedule never fired.

    croniter is a soft dependency — if not installed the scheduler is
    unavailable and a clear error is logged at startup.
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
_MAX_SLEEP_SECONDS = 30.0


def next_run_after(cron_expression: str, after: datetime) -> datetime:
    """Next local due time strictly after `after` (aware datetime in _TZ)."""
    from croniter import croniter

    return croniter(cron_expression, after.astimezone(_TZ)).get_next(datetime)


class SchedulerThread(threading.Thread):
    """Daemon thread that fires on_run() according to a cron expression."""

    def __init__(self, cron_expression: str, on_run: Callable[[], None]) -> None:
        super().__init__(name="shareomat-scheduler", daemon=True)
        self._cron = cron_expression
        self._on_run = on_run
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._stop_event.set()

    def run(self) -> None:
        """Thread main loop: sleep until the next cron occurrence, fire, repeat."""
        try:
            import croniter  # noqa: F401
        except ImportError:
            logger.error(
                "croniter is not installed — cron scheduler disabled. "
                "Install it with: pip install croniter"
            )
            return

        try:
            due = next_run_after(self._cron, datetime.now(_TZ))
        except Exception as exc:
            logger.error("Cron scheduler disabled — invalid expression %r: %s", self._cron, exc)
            return
        logger.info("Cron scheduler started: %s (local time %s), next run %s", self._cron, _TZ.key, due.isoformat())

        while not self._stop_event.is_set():
            remaining = (due - datetime.now(_TZ)).total_seconds()
            if remaining > 0:
                self._stop_event.wait(timeout=min(remaining, _MAX_SLEEP_SECONDS))
                continue
            logger.info("Cron trigger at %s — starting settlement run", due.isoformat())
            threading.Thread(target=self._on_run, name="shareomat-cron-run", daemon=True).start()
            due = next_run_after(self._cron, due)

        logger.info("Cron scheduler stopped")
