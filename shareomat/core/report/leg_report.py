# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_report.py

Purpose:
    Report generation for Shareomat settlement results.
    Writes five report files per settlement cycle:
        billing_*.csv           — per-participant cost breakdown
        billing_*.json          — same data, machine-readable
        match_detail_*.csv      — per-slot energy flow per meter
        community_audit_*.csv   — energy balance check (all zeros = correct)
        community_summary_*.json — community-level KPIs

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    This module is the ONLY place where rounding is applied.
    All monetary values are rounded to 4 decimal places (CHF precision).
    All energy values are rounded to 4 decimal places (kWh precision).
    Timestamps are converted from UTC to the configured display timezone.

    Timezone is read from the SHAREOMAT_TZ environment variable, defaulting
    to Europe/Zurich. The HA add-on sets this from the timezone option.

    Community audit invariants (balance_* fields must all be 0.0):
        balance_export_kwh   = total_export - local_shared - grid_export
        balance_import_kwh   = total_import - local_shared - grid_import
        settlement_balance_kwh = sum_local_supplied - sum_local_received
"""

from __future__ import annotations

import csv
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.models.invoice import BillingRecord, MatchResult

logger = logging.getLogger(__name__)

_TZ_CH = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))

_KWH_PLACES = 4
_CHF_PLACES = 4
_PCT_PLACES = 2

_BILLING_FIELDS = [
    "participant_id", "label", "meter_ids", "period_start", "period_end",
    "slot_count",
    # Export side
    "total_export_kwh", "local_supplied_kwh", "grid_export_kwh",
    # Import side
    "total_import_kwh", "local_received_kwh", "grid_import_kwh",
    # Cost
    "local_rate_chf", "grid_rate_chf",
    "local_cost_chf", "grid_cost_chf", "total_cost_chf",
    "created_at",
]

_MATCH_FIELDS = [
    "slot_start",
    "total_export_kwh", "total_import_kwh",
    "local_shared_kwh",
    "unmatched_export_kwh", "unmatched_import_kwh",
]

_AUDIT_FIELDS = [
    "period_start", "period_end",
    "total_export_kwh", "local_shared_kwh", "grid_export_kwh",
    "total_import_kwh", "grid_import_kwh",
    "sum_local_supplied_kwh", "sum_local_received_kwh",
    "balance_export_kwh", "balance_import_kwh", "settlement_balance_kwh",
]


# ── Private helpers ───────────────────────────────────────────────────────────


def _to_ch(dt: datetime) -> str:
    """Convert a UTC datetime to a Europe/Zurich ISO string for report output."""
    return dt.astimezone(_TZ_CH).isoformat()


def _stem(prefix: str, period_start: datetime) -> str:
    """Build a dated filename stem like 'billing_20240601_120000' for a report file."""
    return f"{prefix}_{period_start.astimezone(_TZ_CH).strftime('%Y%m%d_%H%M%S')}"


def _round_kwh(v: float) -> float:
    """Round an energy value to kWh precision."""
    return round(v, _KWH_PLACES)


def _round_chf(v: float) -> float:
    """Round a monetary value to CHF precision."""
    return round(v, _CHF_PLACES)


# ── Billing reports ───────────────────────────────────────────────────────────


def write_billing_csv(
    records: list[BillingRecord],
    reports_dir: Path,
    period_start: datetime,
) -> Path:
    """Write one billing row per participant to a timestamped CSV report file."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{_stem('billing', period_start)}.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_BILLING_FIELDS)
        writer.writeheader()
        for r in records:
            writer.writerow({
                "participant_id":   r.participant_id,
                "label":            r.label,
                "meter_ids":        ";".join(r.meter_ids),
                "period_start":     _to_ch(r.period_start),
                "period_end":       _to_ch(r.period_end),
                "slot_count":       r.slot_count,
                "total_export_kwh":   _round_kwh(r.total_export_kwh),
                "local_supplied_kwh": _round_kwh(r.local_supplied_kwh),
                "grid_export_kwh":    _round_kwh(r.grid_export_kwh),
                "total_import_kwh":   _round_kwh(r.total_import_kwh),
                "local_received_kwh": _round_kwh(r.local_received_kwh),
                "grid_import_kwh":    _round_kwh(r.grid_import_kwh),
                "local_rate_chf":   r.local_rate_chf,
                "grid_rate_chf":    r.grid_rate_chf,
                "local_cost_chf":   _round_chf(r.local_cost_chf),
                "grid_cost_chf":    _round_chf(r.grid_cost_chf),
                "total_cost_chf":   _round_chf(r.total_cost_chf),
                "created_at":       _to_ch(r.created_at),
            })
    logger.info("Billing CSV written: %s", path.name)
    return path


def write_billing_json(
    records: list[BillingRecord],
    reports_dir: Path,
    period_start: datetime,
) -> Path:
    """Write all billing records as JSON to a timestamped report file."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{_stem('billing', period_start)}.json"
    payload = [
        {
            "participant_id":   r.participant_id,
            "label":            r.label,
            "meter_ids":        r.meter_ids,
            "period_start":     _to_ch(r.period_start),
            "period_end":       _to_ch(r.period_end),
            "slot_count":       r.slot_count,
            "source_files":     r.source_files,
            "total_export_kwh":   _round_kwh(r.total_export_kwh),
            "local_supplied_kwh": _round_kwh(r.local_supplied_kwh),
            "grid_export_kwh":    _round_kwh(r.grid_export_kwh),
            "total_import_kwh":   _round_kwh(r.total_import_kwh),
            "local_received_kwh": _round_kwh(r.local_received_kwh),
            "grid_import_kwh":    _round_kwh(r.grid_import_kwh),
            "local_rate_chf":   r.local_rate_chf,
            "grid_rate_chf":    r.grid_rate_chf,
            "local_cost_chf":   _round_chf(r.local_cost_chf),
            "grid_cost_chf":    _round_chf(r.grid_cost_chf),
            "total_cost_chf":   _round_chf(r.total_cost_chf),
            "created_at":       _to_ch(r.created_at),
        }
        for r in records
    ]
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
    logger.info("Billing JSON written: %s", path.name)
    return path


# ── Match detail report ───────────────────────────────────────────────────────


def write_match_csv(
    results: list[MatchResult],
    reports_dir: Path,
    period_start: datetime,
) -> Path:
    """Write per-slot sharing detail to CSV (one row per 15-minute interval)."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{_stem('match_detail', period_start)}.csv"

    all_consumer_ids = sorted({m for r in results for m in r.meter_local_received_kwh})
    all_supplier_ids = sorted({m for r in results for m in r.meter_local_supplied_kwh})

    fieldnames = (
        _MATCH_FIELDS
        + [f"local_received_{m}" for m in all_consumer_ids]
        + [f"grid_import_{m}" for m in all_consumer_ids]
        + [f"local_supplied_{m}" for m in all_supplier_ids]
        + [f"grid_export_{m}" for m in all_supplier_ids]
    )

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            row: dict[str, object] = {
                "slot_start":          _to_ch(r.slot_start),
                "total_export_kwh":    _round_kwh(r.total_export_kwh),
                "total_import_kwh":    _round_kwh(r.total_import_kwh),
                "local_shared_kwh":    _round_kwh(r.local_shared_kwh),
                "unmatched_export_kwh": _round_kwh(r.unmatched_export_kwh),
                "unmatched_import_kwh": _round_kwh(r.unmatched_import_kwh),
            }
            for m in all_consumer_ids:
                row[f"local_received_{m}"] = _round_kwh(r.meter_local_received_kwh.get(m, 0.0))
                row[f"grid_import_{m}"]    = _round_kwh(r.meter_grid_import_kwh.get(m, 0.0))
            for m in all_supplier_ids:
                row[f"local_supplied_{m}"] = _round_kwh(r.meter_local_supplied_kwh.get(m, 0.0))
                row[f"grid_export_{m}"]    = _round_kwh(r.meter_grid_export_kwh.get(m, 0.0))
            writer.writerow(row)

    logger.info("Match detail CSV written: %s", path.name)
    return path


# ── Community audit report ────────────────────────────────────────────────────


def write_community_audit_csv(
    records: list[BillingRecord],
    reports_dir: Path,
    period_start: datetime,
) -> Path:
    """Write the energy balance audit to CSV — all balance fields must be 0.0."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{_stem('community_audit', period_start)}.csv"

    total_export      = sum(r.total_export_kwh    for r in records)
    total_import      = sum(r.total_import_kwh    for r in records)
    sum_supplied      = sum(r.local_supplied_kwh  for r in records)
    sum_received      = sum(r.local_received_kwh  for r in records)
    grid_export       = sum(r.grid_export_kwh     for r in records)
    grid_import       = sum(r.grid_import_kwh     for r in records)
    local_shared      = sum_supplied   # = sum_received by construction

    balance_export     = _round_kwh(total_export - local_shared - grid_export)
    balance_import     = _round_kwh(total_import - local_shared - grid_import)
    settlement_balance = _round_kwh(sum_supplied - sum_received)

    period_end = records[0].period_end if records else period_start

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_AUDIT_FIELDS)
        writer.writeheader()
        writer.writerow({
            "period_start":          _to_ch(period_start),
            "period_end":            _to_ch(period_end),
            "total_export_kwh":      _round_kwh(total_export),
            "local_shared_kwh":      _round_kwh(local_shared),
            "grid_export_kwh":       _round_kwh(grid_export),
            "total_import_kwh":      _round_kwh(total_import),
            "grid_import_kwh":       _round_kwh(grid_import),
            "sum_local_supplied_kwh": _round_kwh(sum_supplied),
            "sum_local_received_kwh": _round_kwh(sum_received),
            "balance_export_kwh":    balance_export,
            "balance_import_kwh":    balance_import,
            "settlement_balance_kwh": settlement_balance,
        })

    if balance_export != 0.0 or balance_import != 0.0 or settlement_balance != 0.0:
        logger.error(
            "Community audit FAILED: balance_export=%.6f  balance_import=%.6f  settlement=%.6f",
            balance_export, balance_import, settlement_balance,
        )
    else:
        logger.info("Community audit OK — energy balance verified (all zeros)")

    logger.info("Community audit CSV written: %s", path.name)
    return path


# ── Community summary report ──────────────────────────────────────────────────


def write_ledger_csv(
    results: list[MatchResult],
    reports_dir: Path,
    period_start: datetime,
    meter_labels: dict[str, str] | None = None,
) -> Path:
    """Write the Energy Ledger report: per-flow detail, per-meter summary, and audit section."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{_stem('energy_ledger', period_start)}.csv"

    def _label(meter_id: str) -> str:
        if meter_labels:
            return meter_labels.get(meter_id, meter_id)
        return meter_id

    def _fmt(v: float) -> str:
        return f"{round(v, 3):.3f}"

    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)

        # ── Section 1: Detail rows ──────────────────────────────────────────
        writer.writerow([
            "Zeit", "Exporteur", "Importeur",
            "Exporteur Export [kWh]", "Importeur Import [kWh]",
            "Verteilerbasis [kWh]", "Schlüssel",
            "Lokal zugeteilt [kWh]",
            "Netz Rest Export [kWh]", "Netz Rest Import [kWh]",
        ])

        for r in results:
            if not r.flows:
                continue

            # Per-meter totals for this slot
            slot_exports: dict[str, float] = {
                exp_id: (
                    r.meter_local_supplied_kwh.get(exp_id, 0.0)
                    + r.meter_grid_export_kwh.get(exp_id, 0.0)
                )
                for exp_id in r.flows
            }
            all_imp_ids = {imp_id for targets in r.flows.values() for imp_id in targets}
            slot_imports: dict[str, float] = {
                imp_id: (
                    r.meter_local_received_kwh.get(imp_id, 0.0)
                    + r.meter_grid_import_kwh.get(imp_id, 0.0)
                )
                for imp_id in all_imp_ids
            }

            # Running import remainder per importer within this slot
            remaining: dict[str, float] = dict(slot_imports)

            for exp_id in sorted(r.flows):
                targets = r.flows[exp_id]
                exp_kwh = slot_exports.get(exp_id, 0.0)
                grid_export = _round_kwh(r.meter_grid_export_kwh.get(exp_id, 0.0))
                eligible = sum(v for k, v in slot_imports.items() if k != exp_id)

                for imp_id in sorted(targets):
                    flow_kwh = targets[imp_id]
                    imp_kwh = slot_imports.get(imp_id, 0.0)
                    remaining[imp_id] -= flow_kwh

                    writer.writerow([
                        _to_ch(r.slot_start),
                        _label(exp_id),
                        _label(imp_id),
                        _fmt(exp_kwh),
                        _fmt(imp_kwh),
                        _fmt(eligible),
                        f"{_fmt(imp_kwh)} / {_fmt(eligible)}",
                        _fmt(flow_kwh),
                        _fmt(grid_export),
                        _fmt(remaining[imp_id]),
                    ])

        # ── Section 2: Per-meter summary ────────────────────────────────────
        writer.writerow([])
        writer.writerow([
            "Zähler",
            "Total Export [kWh]", "Lokal verkauft [kWh]", "Netzeinspeisung [kWh]",
            "Total Import [kWh]", "Lokal erhalten [kWh]", "Netzbezug [kWh]",
        ])

        totals: dict[str, dict[str, float]] = {}

        def _ensure(mid: str) -> dict[str, float]:
            if mid not in totals:
                totals[mid] = {
                    "local_supplied": 0.0, "grid_export": 0.0,
                    "local_received": 0.0, "grid_import": 0.0,
                }
            return totals[mid]

        for r in results:
            for mid, v in r.meter_local_supplied_kwh.items():
                _ensure(mid)["local_supplied"] += v
            for mid, v in r.meter_grid_export_kwh.items():
                _ensure(mid)["grid_export"] += v
            for mid, v in r.meter_local_received_kwh.items():
                _ensure(mid)["local_received"] += v
            for mid, v in r.meter_grid_import_kwh.items():
                _ensure(mid)["grid_import"] += v

        sum_row = {k: 0.0 for k in ("local_supplied", "grid_export", "local_received", "grid_import")}

        for mid in sorted(totals, key=_label):
            m = totals[mid]
            total_exp = m["local_supplied"] + m["grid_export"]
            total_imp = m["local_received"] + m["grid_import"]
            for k in sum_row:
                sum_row[k] += m[k]
            writer.writerow([
                _label(mid),
                _round_kwh(total_exp),
                _round_kwh(m["local_supplied"]),
                _round_kwh(m["grid_export"]),
                _round_kwh(total_imp),
                _round_kwh(m["local_received"]),
                _round_kwh(m["grid_import"]),
            ])

        sum_exp = sum_row["local_supplied"] + sum_row["grid_export"]
        sum_imp = sum_row["local_received"] + sum_row["grid_import"]
        writer.writerow([
            "SUMME",
            _round_kwh(sum_exp),
            _round_kwh(sum_row["local_supplied"]),
            _round_kwh(sum_row["grid_export"]),
            _round_kwh(sum_imp),
            _round_kwh(sum_row["local_received"]),
            _round_kwh(sum_row["grid_import"]),
        ])

        # ── Section 3: Audit ────────────────────────────────────────────────
        writer.writerow([])
        writer.writerow(["Prüfung", "Wert"])

        n_slots = len(results)
        n_entries = sum(len(targets) for r in results for targets in r.flows.values())
        self_supply = sum(
            1 for r in results
            for exp_id, targets in r.flows.items()
            for imp_id in targets
            if imp_id == exp_id
        )
        balance_err = abs(sum_row["local_supplied"] - sum_row["local_received"])
        ok = self_supply == 0 and balance_err < 1e-6

        writer.writerow(["Anzahl Time Slots", n_slots])
        writer.writerow(["Anzahl Ledger-Einträge", n_entries])
        writer.writerow(["Total Export [kWh]", _round_kwh(sum_exp)])
        writer.writerow(["Total lokal verteilt [kWh]", _round_kwh(sum_row["local_supplied"])])
        writer.writerow(["Total Netzeinspeisung [kWh]", _round_kwh(sum_row["grid_export"])])
        writer.writerow(["Total Import [kWh]", _round_kwh(sum_imp)])
        writer.writerow(["Total lokal erhalten [kWh]", _round_kwh(sum_row["local_received"])])
        writer.writerow(["Total Netzbezug [kWh]", _round_kwh(sum_row["grid_import"])])
        writer.writerow(["Selbstbelieferungen erkannt", self_supply])
        writer.writerow(["Bilanzfehler", 0 if ok else _round_kwh(balance_err)])
        writer.writerow(["Berechnung erfolgreich", "JA" if ok else "NEIN"])

    logger.info("Energy Ledger CSV written: %s", path.name)
    return path


def write_community_summary_json(
    records: list[BillingRecord],
    community_id: str,
    community_name: str,
    reports_dir: Path,
    period_start: datetime,
) -> Path:
    """Write community-level KPI summary to JSON."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{_stem('community_summary', period_start)}.json"

    total_export  = sum(r.total_export_kwh   for r in records)
    total_import  = sum(r.total_import_kwh   for r in records)
    sum_supplied  = sum(r.local_supplied_kwh for r in records)
    sum_received  = sum(r.local_received_kwh for r in records)
    grid_export   = sum(r.grid_export_kwh    for r in records)
    grid_import   = sum(r.grid_import_kwh    for r in records)
    local_shared  = sum_supplied

    self_consumption_pct = (
        round(local_shared / total_export * 100, _PCT_PLACES) if total_export > 0 else 0.0
    )
    autarky_pct = (
        round(local_shared / total_import * 100, _PCT_PLACES) if total_import > 0 else 0.0
    )

    period_end  = records[0].period_end  if records else period_start
    slot_count  = records[0].slot_count  if records else 0

    payload = {
        "community_id":   community_id,
        "community_name": community_name,
        "period_start":   _to_ch(period_start),
        "period_end":     _to_ch(period_end),
        "slot_count":     slot_count,
        "total_export_kwh":       _round_kwh(total_export),
        "local_shared_kwh":       _round_kwh(local_shared),
        "grid_export_kwh":        _round_kwh(grid_export),
        "total_import_kwh":       _round_kwh(total_import),
        "grid_import_kwh":        _round_kwh(grid_import),
        "sum_local_supplied_kwh": _round_kwh(sum_supplied),
        "sum_local_received_kwh": _round_kwh(sum_received),
        "self_consumption_ratio_pct": self_consumption_pct,
        "autarky_ratio_pct":          autarky_pct,
        "settlement_balance_ok":  abs(sum_supplied - sum_received) < 1e-6,
    }

    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    logger.info(
        "Community summary written: %s  (self-consumption=%.1f%%  autarky=%.1f%%)",
        path.name, self_consumption_pct, autarky_pct,
    )
    return path
