# -*- coding: utf-8 -*-
"""
File: shareomat/database/billing.py

Purpose:
    The interactive "Abrechnungen" (billing) workflow: check which meter
    data is available for a user-chosen period, compute a live preview,
    save it as an immutable draft, release it, or cancel it. Reuses the
    existing settlement core (leg_parser/leg_matcher/leg_billing)
    unchanged — only the persistence/versioning around it is new.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    This module is the one deliberate exception to "database/ modules
    contain no core logic": computing a billing preview inherently needs
    both file parsing (core) and period/version bookkeeping (SQL), and
    splitting that across two modules would be more ceremony than
    clarity — the same reasoning already applied to
    shareomat.database.config_builder.

    A BillingRun is immutable once created: draft -> released is a
    one-way transition, and draft|released -> cancelled never edits
    existing billing_records/billing_sources rows, only the run's status.
    There is no function that updates billing_records after creation —
    that is what makes "a released run can't be overwritten" true by
    construction, not just by convention.

    Only local_amount_chf is ever summed into total_leg_amount_chf — the
    amount Shareomat actually bills. Grid consumption
    (grid_amount_chf/total_grid_amount_chf) is informational only: EBL
    bills that directly, so it is deliberately never added to the LEG
    total (see SHAREOMAT_UMBAU_STRUKTUR-era conversation notes).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from shareomat.config import RuntimeConfig
from shareomat.core.collector.leg_import import scan_inbox
from shareomat.core.leg_runner import parse_file
from shareomat.core.pipeline.leg_billing import compute_billing
from shareomat.core.pipeline.leg_matcher import match_all
from shareomat.core.pipeline.leg_parser import readings_to_slots
from shareomat.database.config_builder import build_leg_config
from shareomat.database.community import ensure_community_row_id
from shareomat.database.meter_readings import save_meter_readings
from shareomat.database.settings import get_operation_settings
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.leg_const import BILLING_STATUS_CANCELLED, BILLING_STATUS_DRAFT, BILLING_STATUS_RELEASED, SLOT_MINUTES
from shareomat.models.billing_workflow import (
    BillingLineItem,
    BillingPreview,
    BillingRun,
    BillingRunDetail,
    BillingSourceFile,
    DataAvailability,
)
from shareomat.models.meter_data import IntervalReading

logger = logging.getLogger(__name__)


class BillingWorkflowError(Exception):
    """Raised for invalid billing-workflow requests (bad period, bad status transition, not found)."""


def _period_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """Convert an inclusive [period_start, period_end] date range to a half-open UTC datetime range."""
    start = datetime.combine(period_start, time.min, tzinfo=timezone.utc)
    end = datetime.combine(period_end, time.min, tzinfo=timezone.utc) + timedelta(days=1)
    return start, end


def _round4(value: float) -> Decimal:
    """Convert a float from the settlement core to Decimal via its rounded string form.

    Never construct Decimal directly from a float — that reproduces the
    float's binary artifacts (Decimal(0.1) != Decimal("0.1")).
    """
    return Decimal(str(round(value, 4)))


def _collect_readings_for_period(
    db_path: Path, runtime: RuntimeConfig, period_start: date, period_end: date,
) -> tuple[list[IntervalReading], list[BillingSourceFile]]:
    """Parse every inbox + archive file and keep only readings inside the period.

    Unlike the automatic engine, this deliberately re-parses ALREADY
    ARCHIVED files too — an interactive billing run is for an arbitrary
    historical period the user picks, not just "whatever is new".
    """
    start_dt, end_dt = _period_bounds(period_start, period_end)
    meter_data_source = get_operation_settings(db_path).meter_data_source

    readings: list[IntervalReading] = []
    sources: list[BillingSourceFile] = []
    seen_sha256: set[str] = set()

    for origin, directory in (("inbox", runtime.paths.inbox), ("archive", runtime.paths.archive)):
        if not directory.exists():
            continue
        for imp in scan_inbox(directory):
            try:
                file_readings = parse_file(imp, SLOT_MINUTES, None, meter_data_source=meter_data_source)
            except Exception as exc:
                logger.warning("Billing scan: failed to parse %s: %s", imp.path.name, exc)
                continue
            in_range = [r for r in file_readings if start_dt <= r.slot_start < end_dt]
            if not in_range:
                continue
            readings.extend(in_range)
            if imp.sha256 not in seen_sha256:
                seen_sha256.add(imp.sha256)
                sources.append(BillingSourceFile(filename=imp.path.name, sha256=imp.sha256, origin=origin))

    save_meter_readings(db_path, readings)
    return readings, sources


def compute_billing_preview(
    db_path: Path, runtime: RuntimeConfig, period_start: date, period_end: date,
) -> BillingPreview:
    """Scan available meter data and compute a live billing preview for a period.

    Raises shareomat.database.config_builder.IncompleteConfigError if
    setup (community/participants/meters/tariff) is incomplete for the
    period, or BillingWorkflowError for an invalid period.
    """
    if period_end < period_start:
        raise BillingWorkflowError("Enddatum darf nicht vor dem Startdatum liegen.")

    config = build_leg_config(db_path, runtime, as_of=period_start)
    readings, sources = _collect_readings_for_period(db_path, runtime, period_start, period_end)

    known_meter_ids = {m.meter_id for m in config.meters}
    meter_ids_with_data = sorted({r.meter_id for r in readings})

    availability = DataAvailability(
        meters_with_data=[m for m in meter_ids_with_data if m in known_meter_ids],
        meters_missing_data=sorted(known_meter_ids - set(meter_ids_with_data)),
        unknown_meter_ids=sorted(set(meter_ids_with_data) - known_meter_ids),
        reading_count=len(readings),
        sources=sources,
    )

    # Readings from meters no longer configured are surfaced above but excluded from
    # the computation itself — matching the automatic engine's "skip" unknown-meter policy.
    billable_readings = [r for r in readings if r.meter_id in known_meter_ids]

    line_items: list[BillingLineItem] = []
    if billable_readings:
        match_results = match_all(readings_to_slots(billable_readings))
        if match_results:
            start_dt, end_dt = _period_bounds(period_start, period_end)
            records = compute_billing(
                match_results, config, start_dt, end_dt,
                source_files=[s.filename for s in sources],
            )
            line_items = [
                BillingLineItem(
                    participant_id=r.participant_id,
                    participant_label=r.label,
                    meter_ids=list(r.meter_ids),
                    local_received_kwh=_round4(r.local_received_kwh),
                    local_amount_chf=_round4(r.local_cost_chf),
                    grid_import_kwh=_round4(r.grid_import_kwh),
                    grid_amount_chf=_round4(r.grid_cost_chf),
                    local_supplied_kwh=_round4(r.local_supplied_kwh),
                    grid_export_kwh=_round4(r.grid_export_kwh),
                )
                for r in records
            ]

    return BillingPreview(
        period_start=period_start,
        period_end=period_end,
        availability=availability,
        tariff_name=config.tariff.name,
        local_rate_chf_kwh=config.tariff.local_rate_chf_kwh,
        grid_rate_chf_kwh=config.tariff.grid_rate_chf_kwh,
        feed_in_rate_chf_kwh=config.tariff.feed_in_rate_chf_kwh,
        line_items=line_items,
    )


# ── Persistence ──────────────────────────────────────────────────────────────


_RUN_SELECT = """
    SELECT billing_runs.*, billing_periods.period_start AS bp_start,
           billing_periods.period_end AS bp_end, billing_periods.label AS bp_label
    FROM billing_runs
    JOIN billing_periods ON billing_periods.id = billing_runs.billing_period_id
"""


def _row_to_run(row) -> BillingRun:
    return BillingRun(
        id=row["id"],
        billing_period_id=row["billing_period_id"],
        version=row["version"],
        status=row["status"],
        community_id=row["community_id"],
        community_name=row["community_name"],
        tariff_name=row["tariff_name"],
        local_rate_chf_kwh=Decimal(row["local_rate_chf_kwh"]),
        grid_rate_chf_kwh=Decimal(row["grid_rate_chf_kwh"]),
        feed_in_rate_chf_kwh=Decimal(row["feed_in_rate_chf_kwh"]),
        participant_count=row["participant_count"],
        total_local_kwh=Decimal(row["total_local_kwh"]),
        total_leg_amount_chf=Decimal(row["total_leg_amount_chf"]),
        total_grid_kwh=Decimal(row["total_grid_kwh"]),
        total_grid_amount_chf=Decimal(row["total_grid_amount_chf"]),
        computed_at=datetime.fromisoformat(row["computed_at"]),
        released_at=datetime.fromisoformat(row["released_at"]) if row["released_at"] else None,
        period_start=str_to_date(row["bp_start"]),
        period_end=str_to_date(row["bp_end"]),
        period_label=row["bp_label"] or "",
    )


def save_draft(db_path: Path, preview: BillingPreview) -> BillingRun:
    """Persist a computed preview as a new draft BillingRun (a new version if the period already has runs)."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            community_row = conn.execute(
                "SELECT community_id, name FROM communities WHERE id = ?", (community_row_id,),
            ).fetchone()

            period_row = conn.execute(
                "SELECT id FROM billing_periods WHERE community_id = ? AND period_start = ? AND period_end = ?",
                (community_row_id, date_to_str(preview.period_start), date_to_str(preview.period_end)),
            ).fetchone()
            if period_row:
                period_id = period_row["id"]
            else:
                cursor = conn.execute(
                    "INSERT INTO billing_periods (community_id, period_start, period_end, label, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (community_row_id, date_to_str(preview.period_start), date_to_str(preview.period_end), "", now),
                )
                period_id = cursor.lastrowid

            version = conn.execute(
                "SELECT COALESCE(MAX(version), 0) + 1 AS v FROM billing_runs WHERE billing_period_id = ?",
                (period_id,),
            ).fetchone()["v"]

            cursor = conn.execute(
                """
                INSERT INTO billing_runs
                    (billing_period_id, version, status, community_id, community_name, tariff_name,
                     local_rate_chf_kwh, grid_rate_chf_kwh, feed_in_rate_chf_kwh, participant_count,
                     total_local_kwh, total_leg_amount_chf, total_grid_kwh, total_grid_amount_chf,
                     computed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_id, version, BILLING_STATUS_DRAFT,
                    community_row["community_id"], community_row["name"], preview.tariff_name,
                    str(preview.local_rate_chf_kwh), str(preview.grid_rate_chf_kwh), str(preview.feed_in_rate_chf_kwh),
                    len(preview.line_items),
                    str(preview.total_local_kwh), str(preview.total_leg_amount_chf),
                    str(preview.total_grid_kwh), str(preview.total_grid_amount_chf),
                    now, now, now,
                ),
            )
            run_id = cursor.lastrowid

            for item in preview.line_items:
                conn.execute(
                    """
                    INSERT INTO billing_records
                        (billing_run_id, participant_id, participant_label, meter_ids,
                         local_received_kwh, local_amount_chf, grid_import_kwh, grid_amount_chf,
                         local_supplied_kwh, grid_export_kwh, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, item.participant_id, item.participant_label, ",".join(item.meter_ids),
                        str(item.local_received_kwh), str(item.local_amount_chf),
                        str(item.grid_import_kwh), str(item.grid_amount_chf),
                        str(item.local_supplied_kwh), str(item.grid_export_kwh), now,
                    ),
                )

            for src in preview.availability.sources:
                conn.execute(
                    "INSERT INTO billing_sources (billing_run_id, filename, sha256, origin, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (run_id, src.filename, src.sha256, src.origin, now),
                )

    logger.info(
        "Billing: saved draft run %d (period %s..%s, version %d, %d participant(s))",
        run_id, preview.period_start, preview.period_end, version, len(preview.line_items),
    )
    return get_billing_run(db_path, run_id).run


def release_billing_run(db_path: Path, run_id: int) -> BillingRun:
    """Release a draft — from this point its billing_records are permanently immutable."""
    with connect(db_path) as conn:
        with conn:
            row = conn.execute("SELECT status FROM billing_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise BillingWorkflowError(f"Abrechnung {run_id} wurde nicht gefunden.")
            if row["status"] != BILLING_STATUS_DRAFT:
                raise BillingWorkflowError(
                    f"Nur Entwürfe können freigegeben werden (aktueller Status: {row['status']})."
                )
            now = now_iso()
            conn.execute(
                "UPDATE billing_runs SET status = ?, released_at = ?, updated_at = ? WHERE id = ?",
                (BILLING_STATUS_RELEASED, now, now, run_id),
            )
    logger.info("Billing: released run %d", run_id)
    return get_billing_run(db_path, run_id).run


def cancel_billing_run(db_path: Path, run_id: int) -> BillingRun:
    """Cancel a draft or a released run. Never edits billing_records — only the status changes."""
    with connect(db_path) as conn:
        with conn:
            row = conn.execute("SELECT status FROM billing_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise BillingWorkflowError(f"Abrechnung {run_id} wurde nicht gefunden.")
            if row["status"] == BILLING_STATUS_CANCELLED:
                raise BillingWorkflowError("Abrechnung ist bereits storniert.")
            now = now_iso()
            conn.execute(
                "UPDATE billing_runs SET status = ?, updated_at = ? WHERE id = ?",
                (BILLING_STATUS_CANCELLED, now, run_id),
            )
    logger.info("Billing: cancelled run %d", run_id)
    return get_billing_run(db_path, run_id).run


def list_billing_runs(db_path: Path) -> list[BillingRun]:
    """Return every billing run (all periods, all statuses), newest first."""
    with connect(db_path) as conn:
        rows = conn.execute(_RUN_SELECT + " ORDER BY billing_runs.id DESC").fetchall()
    return [_row_to_run(r) for r in rows]


def get_billing_run(db_path: Path, run_id: int) -> BillingRunDetail:
    """Return one billing run with its full line items and source files."""
    with connect(db_path) as conn:
        row = conn.execute(_RUN_SELECT + " WHERE billing_runs.id = ?", (run_id,)).fetchone()
        if row is None:
            raise BillingWorkflowError(f"Abrechnung {run_id} wurde nicht gefunden.")
        record_rows = conn.execute(
            "SELECT * FROM billing_records WHERE billing_run_id = ? ORDER BY participant_label",
            (run_id,),
        ).fetchall()
        source_rows = conn.execute(
            "SELECT * FROM billing_sources WHERE billing_run_id = ? ORDER BY filename",
            (run_id,),
        ).fetchall()

    line_items = [
        BillingLineItem(
            id=r["id"],
            participant_id=r["participant_id"],
            participant_label=r["participant_label"],
            meter_ids=r["meter_ids"].split(",") if r["meter_ids"] else [],
            local_received_kwh=Decimal(r["local_received_kwh"]),
            local_amount_chf=Decimal(r["local_amount_chf"]),
            grid_import_kwh=Decimal(r["grid_import_kwh"]),
            grid_amount_chf=Decimal(r["grid_amount_chf"]),
            local_supplied_kwh=Decimal(r["local_supplied_kwh"]),
            grid_export_kwh=Decimal(r["grid_export_kwh"]),
        )
        for r in record_rows
    ]
    sources = [
        BillingSourceFile(id=r["id"], filename=r["filename"], sha256=r["sha256"], origin=r["origin"])
        for r in source_rows
    ]
    return BillingRunDetail(run=_row_to_run(row), line_items=line_items, sources=sources)
