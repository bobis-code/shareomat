# -*- coding: utf-8 -*-
"""
File: shareomat/database/exchange_rates.py

Purpose:
    Persist and list fetched SNB exchange rates, so a fetch produces a
    real, browsable table instead of only a one-line log entry.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Reuses shareomat.external_data.market_forecast.ExchangeRate directly
    as both the input and output type — there is no separate "stored"
    shape, the fetched value simply gets an UPSERT home keyed by
    (currency, period): re-fetching the same period updates it in place.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso
from shareomat.external_data.market_forecast import ExchangeRate


def _row_to_rate(row: sqlite3.Row) -> ExchangeRate:
    return ExchangeRate(period=row["period"], rate_chf_per_eur=Decimal(row["rate_chf"]))


def save_exchange_rates(db_path: Path, currency: str, rates: list[ExchangeRate], *, source: str = "SNB") -> None:
    """Insert or update one or more exchange rates for `currency`."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            for rate in rates:
                conn.execute(
                    """
                    INSERT INTO exchange_rates (currency, period, rate_chf, source, fetched_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(currency, period)
                    DO UPDATE SET rate_chf = excluded.rate_chf, fetched_at = excluded.fetched_at
                    """,
                    (currency, rate.period, str(rate.rate_chf_per_eur), source, now, now),
                )


def list_exchange_rates(db_path: Path, *, currency: str = "EUR", limit: int = 36) -> list[ExchangeRate]:
    """Return stored exchange rates for `currency`, most recent period first."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM exchange_rates WHERE currency = ? ORDER BY period DESC LIMIT ?",
            (currency, limit),
        ).fetchall()
    return [_row_to_rate(r) for r in rows]
