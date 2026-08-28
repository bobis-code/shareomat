# -*- coding: utf-8 -*-
"""
File: shareomat/database/day_ahead_prices.py

Purpose:
    Persistence for native-resolution ENTSO-E day-ahead price points (see
    shareomat.core.pipeline.export_price_forecast for the fetch/convert
    pipeline). A rolling, upserted series - not an immutable historical
    fact - so re-fetching overwrites the previous value for the same slot
    rather than accumulating rows. Deliberately separate from
    shareomat.database.price_forecasts (a single averaged value per date
    range, dashboard-only) - Sparkplug LEG/ExportPrice must never see an
    artificially daily-aggregated curve.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso
from shareomat.models.external_data import DayAheadPricePoint


def _row_to_point(row: sqlite3.Row) -> DayAheadPricePoint:
    return DayAheadPricePoint(
        id=row["id"],
        slot_start=datetime.fromisoformat(row["slot_start"]),
        price_chf_kwh=Decimal(row["price_chf_kwh"]),
    )


def save_day_ahead_prices(db_path: Path, points: list[DayAheadPricePoint]) -> int:
    """Upsert native-resolution price points; returns the number of points written."""
    if not points:
        return 0
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            conn.executemany(
                """
                INSERT INTO day_ahead_prices (slot_start, price_chf_kwh, computed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(slot_start)
                DO UPDATE SET price_chf_kwh = excluded.price_chf_kwh, computed_at = excluded.computed_at
                """,
                [(p.slot_start.isoformat(), str(p.price_chf_kwh), now) for p in points],
            )
    return len(points)


def list_day_ahead_prices(
    db_path: Path, *, start: datetime | None = None, end: datetime | None = None,
) -> list[DayAheadPricePoint]:
    """Return native-resolution price points, optionally filtered by time range."""
    query = "SELECT * FROM day_ahead_prices WHERE 1=1"
    params: list = []
    if start is not None:
        query += " AND slot_start >= ?"
        params.append(start.isoformat())
    if end is not None:
        query += " AND slot_start < ?"
        params.append(end.isoformat())
    query += " ORDER BY slot_start"
    with connect(db_path) as conn:
        return [_row_to_point(r) for r in conn.execute(query, params).fetchall()]


def latest_fetched_at(db_path: Path) -> datetime | None:
    """Return the most recent computed_at (fetch time), or None if never fetched."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT MAX(computed_at) AS latest FROM day_ahead_prices").fetchone()
    return datetime.fromisoformat(row["latest"]) if row and row["latest"] else None
