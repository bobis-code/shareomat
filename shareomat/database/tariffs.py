# -*- coding: utf-8 -*-
"""
File: shareomat/database/tariffs.py

Purpose:
    Read and write tariffs (versioned CHF/kWh rates) in SQLite, and
    resolve the tariff valid for a given date.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Rates round-trip as Decimal (stored as TEXT) so no float rounding
    error can creep in between the database and the admin UI. The
    settlement core converts to float once, at the point of arithmetic —
    see shareomat.database.config_builder.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.models.tariff import Tariff


def _row_to_tariff(row: sqlite3.Row) -> Tariff:
    return Tariff(
        local_rate_chf_kwh=Decimal(row["local_rate_chf_kwh"]),
        grid_rate_chf_kwh=Decimal(row["grid_rate_chf_kwh"]),
        feed_in_rate_chf_kwh=Decimal(row["feed_in_rate_chf_kwh"]),
        name=row["name"],
        valid_from=str_to_date(row["valid_from"]),
        valid_until=str_to_date(row["valid_until"]),
        active=bool(row["active"]),
        id=row["id"],
    )


def list_tariffs(db_path: Path, *, include_inactive: bool = True) -> list[Tariff]:
    """Return all tariffs, newest valid_from first."""
    query = "SELECT * FROM tariffs"
    if not include_inactive:
        query += " WHERE active = 1"
    query += " ORDER BY valid_from DESC, id DESC"
    with connect(db_path) as conn:
        rows = conn.execute(query).fetchall()
        return [_row_to_tariff(r) for r in rows]


def get_tariff(db_path: Path, tariff_id: int) -> Tariff | None:
    """Return one tariff by its internal database id, or None if it does not exist."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM tariffs WHERE id = ?", (tariff_id,)).fetchone()
        return _row_to_tariff(row) if row else None


def create_tariff(db_path: Path, tariff: Tariff) -> Tariff:
    """Insert a new tariff version."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            cursor = conn.execute(
                """
                INSERT INTO tariffs
                    (community_id, name, local_rate_chf_kwh, grid_rate_chf_kwh,
                     feed_in_rate_chf_kwh, valid_from, valid_until, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    community_row_id, tariff.name, str(tariff.local_rate_chf_kwh),
                    str(tariff.grid_rate_chf_kwh), str(tariff.feed_in_rate_chf_kwh),
                    date_to_str(tariff.valid_from), date_to_str(tariff.valid_until),
                    int(tariff.active), now, now,
                ),
            )
            new_id = cursor.lastrowid
    result = get_tariff(db_path, new_id)
    assert result is not None
    return result


def update_tariff(db_path: Path, tariff_id: int, tariff: Tariff) -> Tariff:
    """Update an existing tariff by its internal database id."""
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                """
                UPDATE tariffs
                SET name=?, local_rate_chf_kwh=?, grid_rate_chf_kwh=?, feed_in_rate_chf_kwh=?,
                    valid_from=?, valid_until=?, active=?, updated_at=?
                WHERE id=?
                """,
                (
                    tariff.name, str(tariff.local_rate_chf_kwh), str(tariff.grid_rate_chf_kwh),
                    str(tariff.feed_in_rate_chf_kwh), date_to_str(tariff.valid_from),
                    date_to_str(tariff.valid_until), int(tariff.active), now_iso(), tariff_id,
                ),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Tarif {tariff_id} wurde nicht gefunden.")
    result = get_tariff(db_path, tariff_id)
    assert result is not None
    return result


def set_tariff_active(db_path: Path, tariff_id: int, active: bool) -> None:
    """Activate or deactivate a tariff without touching its other fields."""
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE tariffs SET active=?, updated_at=? WHERE id=?",
                (int(active), now_iso(), tariff_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Tarif {tariff_id} wurde nicht gefunden.")


def get_tariff_for_date(db_path: Path, as_of: date) -> Tariff | None:
    """Return the active tariff whose validity window covers `as_of`, or None if none matches.

    If several active tariffs match (overlapping ranges — a data entry
    mistake), the one with the latest valid_from wins, matching "the most
    recently started rate applies".
    """
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM tariffs
            WHERE active = 1
              AND valid_from <= ?
              AND (valid_until IS NULL OR valid_until >= ?)
            ORDER BY valid_from DESC, id DESC
            LIMIT 1
            """,
            (date_to_str(as_of), date_to_str(as_of)),
        ).fetchone()
        return _row_to_tariff(row) if row else None
