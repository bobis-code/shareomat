# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_scheduler.py

Purpose:
    Background thread that fires a settlement run on a cron schedule.
    Uses croniter for schedule evaluation when available.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The thread wakes every 60 seconds, checks if the cron expression matches
    the current minute, and if so calls on_run() in a separate thread to
    avoid blocking the scheduler loop.

    croniter is a soft dependency — if not installed the scheduler is
    unavailable and a clear error is logged at startup.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable

logger = logging.getLogger(__name__)


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
        """Thread main loop: check the cron expression every 60 s and fire on match."""
        try:
            from croniter import croniter
        except ImportError:
            logger.error(
                "croniter is not installed — cron scheduler disabled. "
                "Install it with: pip install croniter"
            )
            return

        logger.info("Cron scheduler started: %s", self._cron)
        last_fired: datetime | None = None

        while not self._stop_event.is_set():
            now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            try:
                it = croniter(self._cron, now)
                prev = it.get_prev(datetime)
                # Fire if the previous scheduled time is within the current minute
                # and we haven't fired for this slot yet
                if prev == now and last_fired != now:
                    last_fired = now
                    logger.info("Cron trigger at %s — starting settlement run", now.isoformat())
                    threading.Thread(
                        target=self._on_run,
                        name="shareomat-cron-run",
                        daemon=True,
                    ).start()
            except Exception as exc:
                logger.error("Cron scheduler error: %s", exc)

            self._stop_event.wait(timeout=60)

        logger.info("Cron scheduler stopped")
