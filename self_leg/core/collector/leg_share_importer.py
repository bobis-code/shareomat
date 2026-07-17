# -*- coding: utf-8 -*-
"""
File: self_leg/core/leg_share_importer.py

Purpose:
    Background thread that copies new meter data files from a configurable
    share folder into the engine inbox. Lets users drop files into a shared
    network location (e.g. /share/self_leg) without direct inbox access.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    The importer copies but never deletes from the share folder — the share
    is treated as read-only. Already-processed files are not re-processed:
    leg_storage SHA256 deduplication handles that downstream.

    A file is copied whenever it exists in share but is absent from inbox.
    If the inbox file was archived, the next scan copies it again — but the
    SHA256 check in leg_storage will silently skip it as already processed.

    Accepted extensions: .csv, .xml, .xlsx (same as the inbox watcher).
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_IMPORT_EXTENSIONS = {".csv", ".xml", ".xlsx"}


class ShareImporterThread(threading.Thread):
    """Daemon thread that copies new files from a share folder into the inbox."""

    def __init__(self, share_path: Path, inbox_path: Path, interval: int = 60) -> None:
        super().__init__(name="self_leg-share-importer", daemon=True)
        self._share = share_path
        self._inbox = inbox_path
        self._interval = interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Signal the thread to stop at the next sleep boundary."""
        self._stop_event.set()

    def _import_new_files(self) -> int:
        """Copy share files not yet present in inbox. Returns count of files copied."""
        if not self._share.exists():
            logger.debug("Share importer: share path does not exist: %s", self._share)
            return 0

        count = 0
        for src in self._share.iterdir():
            if not src.is_file() or src.suffix.lower() not in _IMPORT_EXTENSIONS:
                continue
            dst = self._inbox / src.name
            if not dst.exists():
                try:
                    self._inbox.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                    logger.info("Share importer: copied %s → inbox", src.name)
                    count += 1
                except Exception as exc:
                    logger.warning("Share importer: failed to copy %s: %s", src.name, exc)
        return count

    def run(self) -> None:
        """Thread main loop: import on startup, then every interval seconds."""
        logger.info(
            "Share importer started — watching %s every %ds", self._share, self._interval
        )

        # Import any files already in share on startup
        copied = self._import_new_files()
        if copied:
            logger.info("Share importer: %d file(s) copied on startup", copied)

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break
            self._import_new_files()

        logger.info("Share importer stopped")
