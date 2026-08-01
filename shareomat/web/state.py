# -*- coding: utf-8 -*-
"""
File: shareomat/web/state.py

Purpose:
    Shared state between the engine and the web server: dashboard
    status/counters, a one-shot flash message, the CSRF token, and the
    filesystem/database paths request handlers need. Replaces the old
    shareomat/ha/web_state.py.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The engine (main.py) updates this singleton after every run; the
    stdlib HTTP server (web/server.py) reads it to render pages. Runtime
    hooks and paths are registered once during app startup.
"""

from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Callable

from shareomat.config import RuntimeConfig


class WebState:
    """Thread-safe shared state between the engine and the web server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = {
            "status": "starting",
            "last_run": "-",
            "inbox_count": 0,
            "report_count": 0,
            "last_error": "",
            "warnings": [],
        }

        self._on_run: Callable[[], None] | None = None
        self._db_path: Path | None = None
        self._runtime: RuntimeConfig | None = None

        self._flash_msg: str = ""
        self._flash_ok: bool = True
        self._run_active: bool = False

        # One CSRF token per process lifetime; embedded as a hidden field in
        # every admin form and checked on every POST in web/server.py.
        self.csrf_token: str = secrets.token_urlsafe(32)

    @property
    def db_path(self) -> Path | None:
        """Return the configured SQLite database path, if the app has finished starting up."""
        with self._lock:
            return self._db_path

    @property
    def runtime(self) -> RuntimeConfig | None:
        """Return the technical runtime configuration (paths/mqtt/email/web)."""
        with self._lock:
            return self._runtime

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
        """Register the function called by the dashboard's "Jetzt ausführen" button."""
        with self._lock:
            self._on_run = callback

    def register_db(self, db_path: Path) -> None:
        """Register the SQLite database path used by every admin page."""
        with self._lock:
            self._db_path = db_path

    def register_runtime(self, runtime: RuntimeConfig) -> None:
        """Register the technical runtime configuration (paths/mqtt/email/web)."""
        with self._lock:
            self._runtime = runtime

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

        threading.Thread(target=_run_and_clear, name="shareomat-web-run", daemon=True).start()
        return True

    def set_flash(self, message: str, *, ok: bool = True) -> None:
        """Store a one-shot flash message shown on the next page load (redirect-after-POST)."""
        with self._lock:
            self._flash_msg = message
            self._flash_ok = ok

    def pop_flash(self) -> tuple[str, bool]:
        """Return and clear the pending flash message."""
        with self._lock:
            msg, ok = self._flash_msg, self._flash_ok
            self._flash_msg = ""
            self._flash_ok = True
            return msg, ok


_state = WebState()


def get_state() -> WebState:
    """Return the process-wide WebState singleton shared by main.py and HTTP handlers."""
    return _state
