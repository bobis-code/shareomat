# -*- coding: utf-8 -*-
"""
File: shareomat/database/audit.py

Purpose:
    Append-only event log for legally relevant actions (contract publish/
    withdraw/end, join recorded, signature recorded, grid-operator
    notification, departure, document access).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Rows are only ever inserted — there is deliberately no update/delete
    function. Callers pass an open connection where the event belongs to
    the same transaction as the change it documents, so a rolled-back
    change never leaves a dangling "it happened" entry.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso


@dataclass
class AuditEvent:
    occurred_at: datetime
    entity: str
    entity_id: str
    action: str
    detail: str
    id: int | None = None


def record_event(conn: sqlite3.Connection, entity: str, entity_id, action: str, detail: str = "") -> None:
    """Append one event inside the caller's transaction."""
    conn.execute(
        "INSERT INTO audit_events (occurred_at, entity, entity_id, action, detail) VALUES (?, ?, ?, ?, ?)",
        (now_iso(), entity, str(entity_id), action, detail),
    )


def log_event(db_path: Path, entity: str, entity_id, action: str, detail: str = "") -> None:
    """Append one event in its own transaction (for actions with no other DB write, e.g. a download)."""
    with connect(db_path) as conn:
        with conn:
            record_event(conn, entity, entity_id, action, detail)


def list_events(db_path: Path, *, entity: str | None = None, entity_id=None, limit: int = 200) -> list[AuditEvent]:
    """Newest first, optionally filtered to one entity."""
    query = "SELECT * FROM audit_events"
    params: list = []
    if entity is not None:
        query += " WHERE entity = ?"
        params.append(entity)
        if entity_id is not None:
            query += " AND entity_id = ?"
            params.append(str(entity_id))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        AuditEvent(datetime.fromisoformat(r["occurred_at"]), r["entity"], r["entity_id"], r["action"], r["detail"], r["id"])
        for r in rows
    ]
