# -*- coding: utf-8 -*-
"""
File: shareomat/database/supplier_tariffs.py

Purpose:
    Read and write grid operator (e.g. EBL) tariffs — these are informational
    and used for comparison/control invoices only; Shareomat itself always
    bills participants using the internal LEG tariff (shareomat.database.tariffs).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.models.external_data import SupplierTariff


def _dec(value) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _row_to_supplier_tariff(row: sqlite3.Row) -> SupplierTariff:
    return SupplierTariff(
        id=row["id"],
        source=row["source"],
        product=row["product"],
        energy_rate_ht_chf_kwh=_dec(row["energy_rate_ht_chf_kwh"]),
        energy_rate_nt_chf_kwh=_dec(row["energy_rate_nt_chf_kwh"]),
        grid_rate_ht_chf_kwh=_dec(row["grid_rate_ht_chf_kwh"]),
        grid_rate_nt_chf_kwh=_dec(row["grid_rate_nt_chf_kwh"]),
        base_price_chf_year=_dec(row["base_price_chf_year"]),
        metering_price_chf_year=_dec(row["metering_price_chf_year"]),
        feed_in_rate_chf_kwh=_dec(row["feed_in_rate_chf_kwh"]),
        hkn_rate_chf_kwh=_dec(row["hkn_rate_chf_kwh"]),
        valid_from=str_to_date(row["valid_from"]),
        valid_until=str_to_date(row["valid_until"]),
        active=bool(row["active"]),
    )


def list_supplier_tariffs(db_path: Path, *, include_inactive: bool = True) -> list[SupplierTariff]:
    """Return all supplier tariffs, newest valid_from first."""
    query = "SELECT * FROM supplier_tariffs"
    if not include_inactive:
        query += " WHERE active = 1"
    query += " ORDER BY valid_from DESC, source, product, id DESC"
    with connect(db_path) as conn:
        return [_row_to_supplier_tariff(r) for r in conn.execute(query).fetchall()]


def create_supplier_tariff(db_path: Path, tariff: SupplierTariff) -> SupplierTariff:
    """Insert a new supplier tariff version fetched from an external source."""
    now = now_iso()

    def _str(v: Decimal | None) -> str | None:
        return str(v) if v is not None else None

    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            cursor = conn.execute(
                """
                INSERT INTO supplier_tariffs
                    (community_id, source, product, energy_rate_ht_chf_kwh, energy_rate_nt_chf_kwh,
                     grid_rate_ht_chf_kwh, grid_rate_nt_chf_kwh, base_price_chf_year,
                     metering_price_chf_year, feed_in_rate_chf_kwh, hkn_rate_chf_kwh,
                     valid_from, valid_until, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    community_row_id, tariff.source, tariff.product,
                    _str(tariff.energy_rate_ht_chf_kwh), _str(tariff.energy_rate_nt_chf_kwh),
                    _str(tariff.grid_rate_ht_chf_kwh), _str(tariff.grid_rate_nt_chf_kwh),
                    _str(tariff.base_price_chf_year), _str(tariff.metering_price_chf_year),
                    _str(tariff.feed_in_rate_chf_kwh), _str(tariff.hkn_rate_chf_kwh),
                    date_to_str(tariff.valid_from), date_to_str(tariff.valid_until),
                    int(tariff.active), now, now,
                ),
            )
            new_id = cursor.lastrowid
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM supplier_tariffs WHERE id = ?", (new_id,)).fetchone()
    return _row_to_supplier_tariff(row)


def get_supplier_tariff(db_path: Path, tariff_id: int) -> SupplierTariff | None:
    """Return one supplier tariff by row id, or None."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM supplier_tariffs WHERE id = ?", (tariff_id,)).fetchone()
    return _row_to_supplier_tariff(row) if row else None


def set_supplier_tariff_active(db_path: Path, tariff_id: int, active: bool) -> None:
    """Activate or deactivate one supplier tariff (inactive ones are not offered as contract reference)."""
    with connect(db_path) as conn:
        with conn:
            conn.execute(
                "UPDATE supplier_tariffs SET active = ?, updated_at = ? WHERE id = ?",
                (int(active), now_iso(), tariff_id),
            )


def delete_supplier_tariff(db_path: Path, tariff_id: int) -> None:
    """Delete one supplier tariff. Contract versions keep their own snapshot, so nothing else breaks."""
    with connect(db_path) as conn:
        with conn:
            conn.execute("DELETE FROM supplier_tariffs WHERE id = ?", (tariff_id,))


def replace_supplier_tariffs(db_path: Path, source: str, valid_from, tariffs: list[SupplierTariff]) -> int:
    """Replace all tariffs of one operator starting on `valid_from` with `tariffs` (re-import is idempotent).

    Returns the number of tariffs stored.
    """
    with connect(db_path) as conn:
        with conn:
            conn.execute(
                "DELETE FROM supplier_tariffs WHERE source = ? AND valid_from = ?",
                (source, date_to_str(valid_from)),
            )
    for tariff in tariffs:
        create_supplier_tariff(db_path, tariff)
    return len(tariffs)
