# -*- coding: utf-8 -*-
"""
File: shareomat/database/price_forecasts.py

Purpose:
    Store computed (non-official) market price forecasts, derived from
    ENTSO-E day-ahead prices/generation profiles and SNB exchange rates —
    for dashboard/planning use only, never for final settlement (use
    shareomat.database.reference_prices for that).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.models.external_data import PriceForecast


def _row_to_forecast(row: sqlite3.Row) -> PriceForecast:
    return PriceForecast(
        id=row["id"],
        period_start=str_to_date(row["period_start"]),
        period_end=str_to_date(row["period_end"]),
        forecast_price_chf_kwh=Decimal(row["forecast_price_chf_kwh"]),
        sources=row["sources"].split(",") if row["sources"] else [],
        completeness_pct=Decimal(row["completeness_pct"]) if row["completeness_pct"] else None,
        computed_at=datetime.fromisoformat(row["computed_at"]),
    )


def list_price_forecasts(db_path: Path, *, limit: int = 50) -> list[PriceForecast]:
    """Return the most recent price forecasts, newest period first."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM price_forecasts ORDER BY period_start DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_forecast(r) for r in rows]


def save_price_forecast(db_path: Path, forecast: PriceForecast) -> PriceForecast:
    """Store one computed price forecast."""
    now = now_iso()
    computed_at = forecast.computed_at.isoformat() if forecast.computed_at else now
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                """
                INSERT INTO price_forecasts
                    (period_start, period_end, forecast_price_chf_kwh, completeness_pct,
                     sources, computed_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    date_to_str(forecast.period_start), date_to_str(forecast.period_end),
                    str(forecast.forecast_price_chf_kwh),
                    str(forecast.completeness_pct) if forecast.completeness_pct is not None else None,
                    ",".join(forecast.sources), computed_at, now,
                ),
            )
            new_id = cursor.lastrowid
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM price_forecasts WHERE id = ?", (new_id,)).fetchone()
    return _row_to_forecast(row)
