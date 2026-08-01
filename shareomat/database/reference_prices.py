# -*- coding: utf-8 -*-
"""
File: shareomat/database/reference_prices.py

Purpose:
    Read and write official reference prices (e.g. BFE's quarterly PV
    reference market price per Art. 15 EnFV), used for final/official
    feed-in settlement — as opposed to the running, non-official forecast
    in shareomat.database.price_forecasts.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.models.external_data import ReferencePrice


def _row_to_reference_price(row: sqlite3.Row) -> ReferencePrice:
    return ReferencePrice(
        id=row["id"],
        technology=row["technology"],
        period_start=str_to_date(row["period_start"]),
        period_end=str_to_date(row["period_end"]),
        price_chf_kwh=Decimal(row["price_chf_kwh"]),
        source=row["source"],
        is_official=bool(row["is_official"]),
        published_at=str_to_date(row["published_at"]),
    )


def list_reference_prices(db_path: Path, *, technology: str | None = None) -> list[ReferencePrice]:
    """Return reference prices, newest period first, optionally filtered by technology."""
    query = "SELECT * FROM reference_prices"
    params: tuple = ()
    if technology:
        query += " WHERE technology = ?"
        params = (technology,)
    query += " ORDER BY period_start DESC"
    with connect(db_path) as conn:
        return [_row_to_reference_price(r) for r in conn.execute(query, params).fetchall()]


def get_reference_price_for_period(db_path: Path, technology: str, as_of) -> ReferencePrice | None:
    """Return the official reference price whose period covers `as_of`, or None."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM reference_prices
            WHERE technology = ? AND period_start <= ? AND period_end >= ?
            ORDER BY period_start DESC
            LIMIT 1
            """,
            (technology, date_to_str(as_of), date_to_str(as_of)),
        ).fetchone()
        return _row_to_reference_price(row) if row else None


def save_reference_price(db_path: Path, price: ReferencePrice) -> ReferencePrice:
    """Insert or replace one (technology, period_start, source) reference price."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            conn.execute(
                """
                INSERT INTO reference_prices
                    (technology, period_start, period_end, price_chf_kwh, source,
                     is_official, published_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(technology, period_start, source)
                DO UPDATE SET period_end = excluded.period_end, price_chf_kwh = excluded.price_chf_kwh,
                              is_official = excluded.is_official, published_at = excluded.published_at
                """,
                (
                    price.technology, date_to_str(price.period_start), date_to_str(price.period_end),
                    str(price.price_chf_kwh), price.source, int(price.is_official),
                    date_to_str(price.published_at), now,
                ),
            )
    result = get_reference_price_for_period(db_path, price.technology, price.period_start)
    assert result is not None
    return result
