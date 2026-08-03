# -*- coding: utf-8 -*-
"""
File: shareomat/database/meter_readings.py

Purpose:
    Durable storage for raw per-interval meter readings (import/export kWh
    per meter per slot), independent of the billing/settlement workflow.
    Populated as a side effect of file processing (both the automatic
    settlement cycle and the manual billing-preview re-parse), so a
    continuous raw-data history accumulates regardless of when/whether a
    billing run happens.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso
from shareomat.models.meter_data import IntervalReading


def _row_to_reading(row: sqlite3.Row) -> IntervalReading:
    return IntervalReading(
        meter_id=row["meter_id"],
        slot_start=datetime.fromisoformat(row["slot_start"]),
        value_kwh=row["value_kwh"],
        direction=row["direction"],
        quality=row["quality"],
        source_file=row["source_file"],
    )


def save_meter_readings(db_path: Path, readings: list[IntervalReading]) -> int:
    """Upsert raw interval readings; returns the number of readings written."""
    if not readings:
        return 0
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            conn.executemany(
                """
                INSERT INTO meter_readings
                    (meter_id, slot_start, direction, value_kwh, quality, source_file, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(meter_id, slot_start, direction)
                DO UPDATE SET value_kwh = excluded.value_kwh, quality = excluded.quality,
                              source_file = excluded.source_file, updated_at = excluded.updated_at
                """,
                [
                    (r.meter_id, r.slot_start.isoformat(), r.direction, r.value_kwh,
                     r.quality, r.source_file, now)
                    for r in readings
                ],
            )
    return len(readings)


def list_meter_readings(
    db_path: Path, *, meter_ids: list[str] | None = None,
    start: datetime | None = None, end: datetime | None = None,
) -> list[IntervalReading]:
    """Return raw interval readings, optionally filtered by meter and time range."""
    query = "SELECT * FROM meter_readings WHERE 1=1"
    params: list = []
    if meter_ids:
        query += f" AND meter_id IN ({','.join('?' * len(meter_ids))})"
        params.extend(meter_ids)
    if start is not None:
        query += " AND slot_start >= ?"
        params.append(start.isoformat())
    if end is not None:
        query += " AND slot_start < ?"
        params.append(end.isoformat())
    query += " ORDER BY slot_start"
    with connect(db_path) as conn:
        return [_row_to_reading(r) for r in conn.execute(query, params).fetchall()]


def aggregate_meter_totals(
    db_path: Path, meter_ids: list[str] | None, start: datetime, end: datetime, *, bucket: str = "day",
) -> list[dict]:
    """Return raw import/export kWh summed per meter per day bucket (real 15-min data, safe to bucket)."""
    if bucket != "day":
        raise ValueError("only 'day' bucketing is currently supported")
    query = """
        SELECT date(slot_start) AS bucket, direction, SUM(value_kwh) AS total_kwh
        FROM meter_readings
        WHERE slot_start >= ? AND slot_start < ?
    """
    params: list = [start.isoformat(), end.isoformat()]
    if meter_ids:
        query += f" AND meter_id IN ({','.join('?' * len(meter_ids))})"
        params.extend(meter_ids)
    query += " GROUP BY bucket, direction ORDER BY bucket"
    with connect(db_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [{"bucket": r["bucket"], "direction": r["direction"], "total_kwh": r["total_kwh"]} for r in rows]
