# -*- coding: utf-8 -*-
"""
File: shareomat/database/settlement_history.py

Purpose:
    Durable, append-only history of the local/grid split already computed
    by the automatic settlement cycle (shareomat.core.leg_runner.run() via
    compute_billing()) — no new billing logic here, only persistence of
    results that already exist. Rows are never updated after insert, so
    later tariff/participant/config changes cannot alter historical
    figures. A (participant_id, period_start, period_end, source_fingerprint)
    uniqueness constraint prevents an identical re-run from double-counting,
    while a genuinely different re-run (corrected source files) is kept as
    an additional version rather than overwriting the old one.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso
from shareomat.models.billing import BillingRecord


@dataclass
class SettlementCycleRecord:
    """One participant's persisted local/grid settlement result for one automatic cycle."""

    run_id: str
    period_start: datetime
    period_end: datetime
    participant_id: str
    participant_label: str
    meter_ids: list[str]
    source_fingerprint: str
    local_received_kwh: float
    grid_import_kwh: float
    local_supplied_kwh: float
    grid_export_kwh: float
    local_cost_chf: Decimal
    grid_cost_chf: Decimal
    total_cost_chf: Decimal
    created_at: datetime


def _row_to_record(row: sqlite3.Row) -> SettlementCycleRecord:
    return SettlementCycleRecord(
        run_id=row["run_id"],
        period_start=datetime.fromisoformat(row["period_start"]),
        period_end=datetime.fromisoformat(row["period_end"]),
        participant_id=row["participant_id"],
        participant_label=row["participant_label"],
        meter_ids=[m for m in row["meter_ids"].split(";") if m],
        source_fingerprint=row["source_fingerprint"],
        local_received_kwh=row["local_received_kwh"],
        grid_import_kwh=row["grid_import_kwh"],
        local_supplied_kwh=row["local_supplied_kwh"],
        grid_export_kwh=row["grid_export_kwh"],
        local_cost_chf=Decimal(row["local_cost_chf"]),
        grid_cost_chf=Decimal(row["grid_cost_chf"]),
        total_cost_chf=Decimal(row["total_cost_chf"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def save_settlement_snapshot(
    db_path: Path, run_id: str, source_fingerprint: str,
    period_start: datetime, period_end: datetime, billing_records: list[BillingRecord],
) -> int:
    """Persist one automatic cycle's already-computed local/grid split per participant."""
    if not billing_records:
        return 0
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            conn.executemany(
                """
                INSERT INTO settlement_cycle_records
                    (run_id, period_start, period_end, participant_id, participant_label,
                     meter_ids, source_fingerprint, local_received_kwh, grid_import_kwh,
                     local_supplied_kwh, grid_export_kwh, local_cost_chf, grid_cost_chf,
                     total_cost_chf, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(participant_id, period_start, period_end, source_fingerprint)
                DO NOTHING
                """,
                [
                    (
                        run_id, period_start.isoformat(), period_end.isoformat(),
                        rec.participant_id, rec.label, ";".join(rec.meter_ids), source_fingerprint,
                        rec.local_received_kwh, rec.grid_import_kwh,
                        rec.local_supplied_kwh, rec.grid_export_kwh,
                        str(round(Decimal(str(rec.local_cost_chf)), 4)),
                        str(round(Decimal(str(rec.grid_cost_chf)), 4)),
                        str(round(Decimal(str(rec.total_cost_chf)), 4)),
                        now,
                    )
                    for rec in billing_records
                ],
            )
    return len(billing_records)


_LATEST_VERSION_CTE = """
    WITH latest AS (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY participant_id, period_start, period_end
            ORDER BY created_at DESC
        ) AS rn
        FROM settlement_cycle_records
    )
"""


def list_settlement_history(
    db_path: Path, *, participant_id: str | None = None,
    start: datetime | None = None, end: datetime | None = None,
) -> list[SettlementCycleRecord]:
    """Return settlement history rows, only the latest version per (participant, period)."""
    query = _LATEST_VERSION_CTE + "SELECT * FROM latest WHERE rn = 1"
    params: list = []
    if participant_id:
        query += " AND participant_id = ?"
        params.append(participant_id)
    if start is not None:
        query += " AND period_end > ?"
        params.append(start.isoformat())
    if end is not None:
        query += " AND period_start < ?"
        params.append(end.isoformat())
    query += " ORDER BY period_start"
    with connect(db_path) as conn:
        return [_row_to_record(r) for r in conn.execute(query, params).fetchall()]


def aggregate_leg_local_grid(db_path: Path, start: datetime, end: datetime) -> list[dict]:
    """Return LEG-wide local/grid kWh summed per real settlement period (latest version only)."""
    query = _LATEST_VERSION_CTE + """
        SELECT period_start, period_end,
               SUM(local_received_kwh) AS local_kwh,
               SUM(grid_import_kwh) AS grid_kwh
        FROM latest
        WHERE rn = 1 AND period_end > ? AND period_start < ?
        GROUP BY period_start, period_end
        ORDER BY period_start
    """
    with connect(db_path) as conn:
        rows = conn.execute(query, (start.isoformat(), end.isoformat())).fetchall()
    return [
        {"period_start": r["period_start"], "period_end": r["period_end"],
         "local_kwh": r["local_kwh"], "grid_kwh": r["grid_kwh"]}
        for r in rows
    ]


def aggregate_participant_local_grid(
    db_path: Path, participant_id: str, start: datetime, end: datetime,
) -> list[dict]:
    """Return one participant's local/grid kWh per real settlement period (latest version only)."""
    query = _LATEST_VERSION_CTE + """
        SELECT period_start, period_end, local_received_kwh AS local_kwh, grid_import_kwh AS grid_kwh
        FROM latest
        WHERE rn = 1 AND participant_id = ? AND period_end > ? AND period_start < ?
        ORDER BY period_start
    """
    with connect(db_path) as conn:
        rows = conn.execute(query, (participant_id, start.isoformat(), end.isoformat())).fetchall()
    return [
        {"period_start": r["period_start"], "period_end": r["period_end"],
         "local_kwh": r["local_kwh"], "grid_kwh": r["grid_kwh"]}
        for r in rows
    ]
