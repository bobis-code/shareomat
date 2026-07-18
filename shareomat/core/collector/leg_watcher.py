# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_watcher.py

Purpose:
    Background thread that watches the inbox directory for new files
    and triggers a settlement run automatically.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Polling-based watcher — checks for new files every scan_interval_seconds.
    A file is "new" if its name was not present in the previous scan.
    Waits 2 seconds after detecting new files before triggering the run,
    to allow partially-written files to complete.

    The watcher can be paused/resumed via pause() and resume() without
    stopping the thread. The MQTT switch (auto_scan/set) controls this.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

# File extensions that count as "inbox files"
_INBOX_EXTENSIONS = {".csv", ".xml", ".xlsx"}


class WatcherThread(threading.Thread):
    """Daemon thread that polls the inbox and triggers on_run() on new files."""

    def __init__(
        self,
        inbox: Path,
        on_run: Callable[[], None],
        interval: int = 60,
    ) -> None:
        super().__init__(name="shareomat-watcher", daemon=True)
        self._inbox = inbox
        self._on_run = on_run
        self._interval = interval
        self._stop_event = threading.Event()
        self._paused = threading.Event()
        self._paused.set()  # not paused by default
        self._known_files: set[str] = set()

    def pause(self) -> None:
        """Pause the watcher (stops triggering runs but keeps polling)."""
        self._paused.clear()
        logger.info("File watcher paused")

    def resume(self) -> None:
        """Resume the watcher."""
        self._paused.set()
        logger.info("File watcher resumed")

    @property
    def is_active(self) -> bool:
        """True when the watcher is running and not paused."""
        return self._paused.is_set()

    def stop(self) -> None:
        """Signal the thread to stop."""
        self._stop_event.set()
        self._paused.set()  # unblock wait

    def _scan(self) -> set[str]:
        if not self._inbox.exists():
            return set()
        return {
            f.name for f in self._inbox.iterdir()
            if f.is_file() and f.suffix.lower() in _INBOX_EXTENSIONS
        }

    def run(self) -> None:
        """Thread main loop: poll inbox every interval seconds and trigger on new files."""
        logger.info(
            "File watcher started — watching %s every %ds", self._inbox, self._interval
        )
        # Initial scan establishes baseline (don't trigger on already-present files)
        self._known_files = self._scan()

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break

            if not self._paused.is_set():
                continue  # paused

            current = self._scan()
            new_files = current - self._known_files
            self._known_files = current

            if new_files:
                logger.info(
                    "File watcher: %d new file(s) detected: %s — triggering run",
                    len(new_files), sorted(new_files),
                )
                time.sleep(2)  # let file finish writing
                threading.Thread(
                    target=self._on_run,
                    name="shareomat-watcher-run",
                    daemon=True,
                ).start()

        logger.info("File watcher stopped")
