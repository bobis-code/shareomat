# -*- coding: utf-8 -*-
"""
File: shareomat/database/consumption_forecasts.py

Purpose:
    Persistence for computed short-term LEG demand forecasts (see
    shareomat.core.pipeline.consumption_forecast for the algorithm). A
    rolling, upserted forecast — not an immutable historical fact like
    shareomat.database.settlement_history — so re-computation overwrites
    the previous value for the same slot rather than accumulating rows.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso
from shareomat.models.forecast import ConsumptionForecastPoint


def _row_to_point(row: sqlite3.Row) -> ConsumptionForecastPoint:
    return ConsumptionForecastPoint(
        scope=row["scope"],
        participant_id=row["participant_id"],
        slot_start=datetime.fromisoformat(row["slot_start"]),
        forecast_kwh=row["forecast_kwh"],
        quality=row["quality"],
        method=row["method"],
        sample_count=row["sample_count"],
        data_period_start=datetime.fromisoformat(row["data_period_start"]) if row["data_period_start"] else None,
        data_period_end=datetime.fromisoformat(row["data_period_end"]) if row["data_period_end"] else None,
        computed_at=datetime.fromisoformat(row["computed_at"]),
    )


def save_consumption_forecast(db_path: Path, points: list[ConsumptionForecastPoint]) -> int:
    """Upsert forecast points; returns the number of points written."""
    if not points:
        return 0
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            conn.executemany(
                """
                INSERT INTO consumption_forecasts
                    (scope, participant_id, slot_start, forecast_kwh, quality, method,
                     sample_count, data_period_start, data_period_end, computed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, participant_id, slot_start)
                DO UPDATE SET forecast_kwh = excluded.forecast_kwh, quality = excluded.quality,
                              method = excluded.method, sample_count = excluded.sample_count,
                              data_period_start = excluded.data_period_start,
                              data_period_end = excluded.data_period_end,
                              computed_at = excluded.computed_at
                """,
                [
                    (
                        p.scope, p.participant_id, p.slot_start.isoformat(), p.forecast_kwh,
                        p.quality, p.method, p.sample_count,
                        p.data_period_start.isoformat() if p.data_period_start else None,
                        p.data_period_end.isoformat() if p.data_period_end else None,
                        now,
                    )
                    for p in points
                ],
            )
    return len(points)


def list_consumption_forecasts(
    db_path: Path, *, scope: str = "leg", participant_id: str = "",
    start: datetime | None = None, end: datetime | None = None,
) -> list[ConsumptionForecastPoint]:
    """Return forecast points for a scope, optionally filtered by time range."""
    query = "SELECT * FROM consumption_forecasts WHERE scope = ? AND participant_id = ?"
    params: list = [scope, participant_id]
    if start is not None:
        query += " AND slot_start >= ?"
        params.append(start.isoformat())
    if end is not None:
        query += " AND slot_start < ?"
        params.append(end.isoformat())
    query += " ORDER BY slot_start"
    with connect(db_path) as conn:
        return [_row_to_point(r) for r in conn.execute(query, params).fetchall()]


def latest_computed_at(db_path: Path, *, scope: str = "leg", participant_id: str = "") -> datetime | None:
    """Return the most recent computed_at for a scope, or None if never computed."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT MAX(computed_at) AS latest FROM consumption_forecasts WHERE scope = ? AND participant_id = ?",
            (scope, participant_id),
        ).fetchone()
    return datetime.fromisoformat(row["latest"]) if row and row["latest"] else None
