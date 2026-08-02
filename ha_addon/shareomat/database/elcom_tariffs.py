# -*- coding: utf-8 -*-
"""
File: shareomat/database/elcom_tariffs.py

Purpose:
    Persist and list fetched ElCom tariff comparisons, so a lookup
    produces a real, browsable table instead of only a one-line log
    entry.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Reuses shareomat.external_data.elcom.ElcomTariffComponents directly
    as both the input and output type. Keyed by
    (municipality_bfs_number, year, category): re-fetching the same
    municipality/year updates its categories in place rather than
    accumulating duplicates.
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso
from shareomat.external_data.elcom import ElcomTariffComponents


def _row_to_tariff(row: sqlite3.Row) -> ElcomTariffComponents:
    return ElcomTariffComponents(
        category=row["category"],
        energy_chf_kwh=Decimal(row["energy_chf_kwh"]),
        grid_chf_kwh=Decimal(row["grid_chf_kwh"]),
        aidfee_chf_kwh=Decimal(row["aidfee_chf_kwh"]),
        community_fees_chf_kwh=Decimal(row["community_fees_chf_kwh"]),
        total_chf_kwh=Decimal(row["total_chf_kwh"]),
        fixcosts_chf_year=Decimal(row["fixcosts_chf_year"]),
        operator_iri=None,
        operator_name=row["operator_name"] or None,
    )


def save_elcom_tariffs(
    db_path: Path, municipality_bfs_number: str, year: int, tariffs: list[ElcomTariffComponents],
) -> None:
    """Insert or update one municipality/year's ElCom tariff categories."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            for tariff in tariffs:
                conn.execute(
                    """
                    INSERT INTO elcom_tariffs
                        (municipality_bfs_number, year, category, energy_chf_kwh, grid_chf_kwh,
                         aidfee_chf_kwh, community_fees_chf_kwh, total_chf_kwh, fixcosts_chf_year,
                         operator_name, fetched_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(municipality_bfs_number, year, category)
                    DO UPDATE SET energy_chf_kwh = excluded.energy_chf_kwh, grid_chf_kwh = excluded.grid_chf_kwh,
                                  aidfee_chf_kwh = excluded.aidfee_chf_kwh,
                                  community_fees_chf_kwh = excluded.community_fees_chf_kwh,
                                  total_chf_kwh = excluded.total_chf_kwh,
                                  fixcosts_chf_year = excluded.fixcosts_chf_year,
                                  operator_name = excluded.operator_name, fetched_at = excluded.fetched_at
                    """,
                    (
                        municipality_bfs_number, year, tariff.category,
                        str(tariff.energy_chf_kwh), str(tariff.grid_chf_kwh),
                        str(tariff.aidfee_chf_kwh), str(tariff.community_fees_chf_kwh),
                        str(tariff.total_chf_kwh), str(tariff.fixcosts_chf_year),
                        tariff.operator_name or "", now, now,
                    ),
                )


def list_elcom_tariffs(
    db_path: Path, *, municipality_bfs_number: str | None = None, limit: int = 50,
) -> list[tuple[str, int, ElcomTariffComponents]]:
    """Return stored ElCom tariffs as (municipality_bfs_number, year, tariff) tuples, newest first."""
    query = "SELECT * FROM elcom_tariffs"
    params: tuple = ()
    if municipality_bfs_number:
        query += " WHERE municipality_bfs_number = ?"
        params = (municipality_bfs_number,)
    query += " ORDER BY year DESC, municipality_bfs_number, category LIMIT ?"
    with connect(db_path) as conn:
        rows = conn.execute(query, params + (limit,)).fetchall()
    return [(r["municipality_bfs_number"], r["year"], _row_to_tariff(r)) for r in rows]
