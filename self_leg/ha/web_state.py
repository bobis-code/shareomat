# -*- coding: utf-8 -*-
"""
Shared state for the Home Assistant Ingress dashboard.

The engine updates this singleton from main.py, while the stdlib HTTP server
reads it to render status, reports, upload feedback, and manual run controls.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable


class IngressState:
    """Thread-safe shared state between the engine and the ingress server."""

    def __init__(self) -> None:
        # Dashboard counters/status are updated by the engine and read by the HTTP thread.
        self._lock = threading.Lock()
        self._data = {
            "status": "starting",
            "last_run": "-",
            "inbox_count": 0,
            "report_count": 0,
            "last_error": "",
            "warnings": [],
        }

        # Runtime hooks and filesystem paths are registered once during app startup.
        self._on_run: Callable[[], None] | None = None
        self._inbox_path: Path | None = None
        self._reports_path: Path | None = None
        self._meter_list: list[tuple[str, str]] = []

        # Upload feedback is consumed once so browser refreshes do not repeat stale notices.
        self._upload_msg: str = ""
        self._upload_ok: bool = True
        self._run_active: bool = False

    @property
    def inbox_path(self) -> Path | None:
        """Return the configured upload inbox path, if Ingress is fully initialized."""
        with self._lock:
            return self._inbox_path

    @property
    def reports_path(self) -> Path | None:
        """Return the report output directory used for dashboard downloads."""
        with self._lock:
            return self._reports_path

    @property
    def meter_list(self) -> list[tuple[str, str]]:
        """Return a copy of configured (meter_id, label) pairs."""
        with self._lock:
            return list(self._meter_list)

    def update(self, **kwargs) -> None:
        """Update one or more state fields atomically."""
        with self._lock:
            self._data.update(kwargs)

    def get(self) -> dict:
        """Return a snapshot of the current state as a plain dict."""
        with self._lock:
            data = dict(self._data)
            data["warnings"] = list(self._data.get("warnings", []))
            return data

    def add_warning(self, message: str) -> None:
        """Add a persistent dashboard warning if it is not already shown."""
        with self._lock:
            warnings = list(self._data.get("warnings", []))
            if message not in warnings:
                warnings.append(message)
            self._data["warnings"] = warnings

    def clear_warnings(self) -> None:
        """Clear persistent dashboard warnings."""
        with self._lock:
            self._data["warnings"] = []

    def register_on_run(self, callback: Callable[[], None]) -> None:
        """Register the function called by the dashboard Run button."""
        with self._lock:
            self._on_run = callback

    def register_inbox(self, inbox: Path) -> None:
        """Register the inbox path so uploaded files can be saved there."""
        with self._lock:
            self._inbox_path = inbox

    def register_reports(self, reports: Path | None) -> None:
        """Register the reports path so the dashboard can display outputs."""
        with self._lock:
            self._reports_path = reports

    def register_meters(self, meters: list[tuple[str, str]]) -> None:
        """Register configured (mpid, label) pairs for dashboard display."""
        with self._lock:
            self._meter_list = list(meters)

    def trigger_run(self) -> bool:
        """Start one settlement run unless another manual run is already active."""
        with self._lock:
            if self._on_run is None or self._run_active:
                return False
            callback = self._on_run
            self._run_active = True

        def _run_and_clear() -> None:
            try:
                callback()
            finally:
                with self._lock:
                    self._run_active = False

        threading.Thread(target=_run_and_clear, name="self_leg-ingress-run", daemon=True).start()
        return True

    def set_upload_result(self, message: str, *, ok: bool = True) -> None:
        """Store a one-shot upload result message shown on the next dashboard load."""
        with self._lock:
            self._upload_msg = message
            self._upload_ok = ok

    def pop_upload_result(self) -> tuple[str, bool]:
        """Return and clear the pending upload result."""
        with self._lock:
            msg, ok = self._upload_msg, self._upload_ok
            self._upload_msg = ""
            self._upload_ok = True
            return msg, ok


_state = IngressState()


def get_state() -> IngressState:
    """Return the process-wide IngressState singleton shared by main.py and HTTP handlers."""
    return _state
