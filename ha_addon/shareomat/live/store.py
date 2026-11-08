# -*- coding: utf-8 -*-
"""
File: shareomat/live/store.py

Purpose:
    live.db: the last valid value per live source and a short event log of
    status changes (ADR-0007, 2 and 3.6). A separate SQLite file with short
    retention. Billing code does not know this file.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    A failure here never stops the live layer: live values are a display,
    the file is only used to show the last valid measurement after a restart.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

EVENT_RETENTION_DAYS = 14
_MAX_EVENTS = 5000

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS live_latest (
        source_id INTEGER PRIMARY KEY,
        community_id TEXT NOT NULL,
        import_w REAL NOT NULL,
        export_w REAL NOT NULL,
        measured_at TEXT NOT NULL,
        received_at TEXT NOT NULL,
        time_origin TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS live_events (
        id INTEGER PRIMARY KEY,
        occurred_at TEXT NOT NULL,
        community_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        detail TEXT NOT NULL
    )
    """,
)


def live_db_path_for(db_path: Path) -> Path:
    """live.db lives next to shareomat.db, in the same protected data directory."""
    return db_path.with_name("live.db")


class LiveStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=2)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            yield conn
        finally:
            conn.close()

    def init(self) -> None:
        with self._connect() as conn:
            with conn:
                for statement in _SCHEMA:
                    conn.execute(statement)

    def save_latest(self, source_id: int, community_id: str, import_w: float, export_w: float,
                    measured_at: datetime, received_at: datetime, time_origin: str | None) -> None:
        with self._connect() as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO live_latest (source_id, community_id, import_w, export_w, measured_at, received_at, time_origin)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id) DO UPDATE SET community_id = excluded.community_id,
                        import_w = excluded.import_w, export_w = excluded.export_w, measured_at = excluded.measured_at,
                        received_at = excluded.received_at, time_origin = excluded.time_origin
                    """,
                    (source_id, community_id, import_w, export_w, measured_at.isoformat(), received_at.isoformat(), time_origin),
                )

    def load_latest(self) -> dict[int, dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM live_latest").fetchall()
        return {
            r["source_id"]: {
                "import_w": r["import_w"], "export_w": r["export_w"],
                "measured_at": datetime.fromisoformat(r["measured_at"]),
                "received_at": datetime.fromisoformat(r["received_at"]),
                "time_origin": r["time_origin"],
            }
            for r in rows
        }

    def add_event(self, community_id: str, kind: str, detail: str, now: datetime | None = None) -> None:
        moment = now or datetime.now(timezone.utc)
        with self._connect() as conn:
            with conn:
                conn.execute(
                    "INSERT INTO live_events (occurred_at, community_id, kind, detail) VALUES (?, ?, ?, ?)",
                    (moment.isoformat(), community_id, kind, detail),
                )

    def recent_events(self, limit: int = 30) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM live_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [{"occurred_at": datetime.fromisoformat(r["occurred_at"]), "kind": r["kind"], "detail": r["detail"]} for r in rows]

    def prune(self, now: datetime | None = None) -> None:
        moment = now or datetime.now(timezone.utc)
        cutoff = (moment - timedelta(days=EVENT_RETENTION_DAYS)).isoformat()
        with self._connect() as conn:
            with conn:
                conn.execute("DELETE FROM live_events WHERE occurred_at < ?", (cutoff,))
                conn.execute(
                    "DELETE FROM live_events WHERE id NOT IN (SELECT id FROM live_events ORDER BY id DESC LIMIT ?)",
                    (_MAX_EVENTS,),
                )
