# -*- coding: utf-8 -*-
"""
File: shareomat/database/data_imports.py

Purpose:
    Audit trail for every external data fetch attempt (EBL, ElCom, BFE,
    ENTSO-E, SNB) — what was fetched, when, and whether it succeeded.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.models.external_data import DataImportRecord


def _row_to_record(row: sqlite3.Row) -> DataImportRecord:
    from datetime import datetime
    return DataImportRecord(
        id=row["id"],
        source=row["source"],
        data_type=row["data_type"],
        status=row["status"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        valid_from=str_to_date(row["valid_from"]),
        detail=row["detail"],
        checksum=row["checksum"],
    )


def record_import(db_path: Path, record: DataImportRecord) -> DataImportRecord:
    """Append one audit-trail entry. Never updates or deletes — a pure log."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO data_imports
                    (source, data_type, fetched_at, valid_from, status, detail, checksum, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.source, record.data_type, record.fetched_at.isoformat() if record.fetched_at else now,
                    date_to_str(record.valid_from), record.status, record.detail, record.checksum, now,
                ),
            )
            new_id = cursor.lastrowid
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM data_imports WHERE id = ?", (new_id,)).fetchone()
    return _row_to_record(row)


def list_recent_imports(db_path: Path, *, limit: int = 20) -> list[DataImportRecord]:
    """Return the most recent import attempts across all sources, newest first."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM data_imports ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_record(r) for r in rows]
