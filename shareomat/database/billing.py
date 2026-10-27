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

import hashlib
import json
import logging
import os
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.config import RuntimeConfig
from shareomat.core.collector.leg_import import scan_inbox
from shareomat.core.leg_runner import parse_file
from shareomat.core.pipeline.leg_billing import compute_billing
from shareomat.core.pipeline.leg_matcher import match_all
from shareomat.core.pipeline.leg_parser import readings_to_slots
from shareomat.database.audit import record_event
from shareomat.database.billing_eligibility import EligibilityResolver
from shareomat.database.config_builder import IncompleteConfigError, build_leg_config
from shareomat.database.community import ensure_community_row_id
from shareomat.database.meter_readings import save_meter_readings
from shareomat.database.meters import list_meters
from shareomat.database.participants import list_participants
from shareomat.database.tariffs import get_tariff_for_date
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


_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
_CHF_CENT = Decimal("0.01")


def _period_bounds(period_start: date, period_end: date) -> tuple[datetime, datetime]:
    """Inclusive [period_start, period_end] LOCAL days (Europe/Zurich) -> half-open UTC datetime range.

    A Swiss billing month starts at local midnight, e.g. 01.11.2026 00:00 CET =
    31.10.2026 23:00 UTC. Taking UTC midnight instead moved the first/last
    hour of every month into the neighbouring period.
    """
    start = datetime.combine(period_start, time.min, tzinfo=_TZ).astimezone(timezone.utc)
    end = datetime.combine(period_end + timedelta(days=1), time.min, tzinfo=_TZ).astimezone(timezone.utc)
    return start, end


def _local_day(slot_start: datetime) -> date:
    return slot_start.astimezone(_TZ).date()


def _slots_in_local_day(day: date) -> int:
    """Quarter hours in one local day: 96 normally, 92 / 100 on the DST switch days."""
    start = datetime.combine(day, time.min, tzinfo=_TZ).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=_TZ).astimezone(timezone.utc)
    return int((end - start).total_seconds() // (SLOT_MINUTES * 60))


def _round4(value: float) -> Decimal:
    """Convert a float from the settlement core to Decimal via its rounded string form.

    Never construct Decimal directly from a float — that reproduces the
    float's binary artifacts (Decimal(0.1) != Decimal("0.1")).
    """
    return Decimal(str(round(value, 4)))


def _chf(value: Decimal) -> Decimal:
    """Round a document amount to 0.01 CHF (commercial rounding)."""
    return value.quantize(_CHF_CENT, rounding=ROUND_HALF_UP)


_REQUIRED_CHANNELS = {
    "consumer": ("import",),
    "producer": ("export",),
    "producer_consumer": ("import", "export"),
    "storage": ("import", "export"),
}


def _largest_remainder(amounts: list[Decimal]) -> list[Decimal]:
    """Round amounts to cents so they sum to the rounded total (largest remainder method).

    Each amount is first rounded down; the cents still missing to reach the
    commercially rounded total go to the amounts with the largest dropped
    remainders (ties: earlier position). E.g. 3 x 0.0154 (total 0.0462 ->
    0.05) -> 0.02 / 0.02 / 0.01 instead of 3 x 0.02 = 0.06.
    """
    if not amounts:
        return []
    floors = [a.quantize(_CHF_CENT, rounding=ROUND_DOWN) for a in amounts]
    target = _chf(sum(amounts, Decimal(0)))
    missing = int(((target - sum(floors, Decimal(0))) / _CHF_CENT).to_integral_value())
    order = sorted(range(len(amounts)), key=lambda i: (-(amounts[i] - floors[i]), i))
    result = list(floors)
    for i in order[:max(missing, 0)]:
        result[i] += _CHF_CENT
    return result


def _allocate_cents(items: list[BillingLineItem]) -> None:
    """Set charge/credit/net per participant so the rounded totals reconcile.

    Σ charges is the commercially rounded total of all consumer amounts,
    Σ credits the rounded total of all producer amounts — so with equal
    prices and no fee Σ charges == Σ credits exactly, and otherwise the
    difference is the (rounded) agreed fee. Prosumers keep both lines and
    are netted.
    """
    charges = _largest_remainder([i.local_amount_chf for i in items])
    credits = _largest_remainder([i.producer_credit_chf for i in items])
    for item, charge, credit in zip(items, charges, credits):
        item.charge_chf, item.credit_chf, item.net_amount_chf = charge, credit, charge - credit


def _result_fingerprint(preview: BillingPreview) -> str:
    """Stable hash of everything a release depends on (amounts, quantities, sources, issues)."""
    payload = {
        "lines": [
            [i.participant_id, str(i.local_received_kwh), str(i.local_amount_chf), str(i.local_supplied_kwh),
             str(i.producer_credit_chf), str(i.charge_chf), str(i.credit_chf), ",".join(i.meter_ids)]
            for i in sorted(preview.line_items, key=lambda x: x.participant_id)
        ],
        "sources": sorted(s.sha256 for s in preview.availability.sources),
        "issues": preview.availability.blocking_issues,
    }
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _collect_readings_for_period(
    db_path: Path, runtime: RuntimeConfig, period_start: date, period_end: date,
) -> tuple[list[IntervalReading], list[BillingSourceFile], list[str]]:
    """Parse every inbox + archive file and keep only readings inside the period, de-duplicated.

    Unlike the automatic engine, this deliberately re-parses ALREADY
    ARCHIVED files too — an interactive billing run is for an arbitrary
    historical period the user picks, not just "whatever is new".

    Each (meter, quarter hour, direction) counts once: a file whose SHA-256
    was already read (same file in inbox and archive) is skipped entirely;
    the same value from two different files is kept once; DIFFERENT values
    for the same key are a conflict — the newest file wins and the conflict
    is returned as a blocking issue (never silently summed). Unparseable
    files are blocking issues too (fail closed).
    """
    start_dt, end_dt = _period_bounds(period_start, period_end)
    meter_data_source = get_operation_settings(db_path).meter_data_source

    files = []
    for origin, directory in (("inbox", runtime.paths.inbox), ("archive", runtime.paths.archive)):
        if directory.exists():
            files.extend((origin, imp) for imp in scan_inbox(directory))
    files.sort(key=lambda item: item[1].path.stat().st_mtime)  # oldest first, newest wins on conflict

    by_key: dict[tuple, IntervalReading] = {}
    origin_of: dict[tuple, str] = {}
    sources: list[BillingSourceFile] = []
    issues: list[str] = []
    seen_sha256: set[str] = set()
    conflicts = 0

    for origin, imp in files:
        if imp.sha256 in seen_sha256:
            continue  # identical file already read (e.g. inbox + archive)
        row_errors: list[str] = []
        try:
            file_readings = parse_file(imp, SLOT_MINUTES, None, meter_data_source=meter_data_source,
                                       errors=row_errors)
        except Exception as exc:
            logger.warning("Billing scan: failed to parse %s: %s", imp.path.name, exc)
            issues.append(f"Datei {imp.path.name} konnte nicht gelesen werden: {exc}")
            continue
        in_range = [r for r in file_readings if start_dt <= r.slot_start < end_dt]
        if not in_range:
            continue
        if row_errors:
            issues.append(
                f"Datei {imp.path.name}: {len(row_errors)} Zeile(n) abgewiesen (z. B. {row_errors[0]})."
            )
        seen_sha256.add(imp.sha256)
        sources.append(BillingSourceFile(filename=imp.path.name, sha256=imp.sha256, origin=origin))
        for r in in_range:
            key = (r.meter_id, r.slot_start, r.direction)
            previous = by_key.get(key)
            if previous is not None and abs(previous.value_kwh - r.value_kwh) > 1e-9:
                conflicts += 1
                if conflicts <= 5:
                    issues.append(
                        f"Widersprüchliche Werte für {r.meter_id} {r.direction} "
                        f"{r.slot_start.astimezone(_TZ).strftime('%d.%m.%Y %H:%M')}: "
                        f"{previous.value_kwh} ({origin_of[key]}) vs. {r.value_kwh} ({imp.path.name}) — neuere Datei verwendet."
                    )
            by_key[key] = r
            origin_of[key] = imp.path.name
    if conflicts > 5:
        issues.append(f"… insgesamt {conflicts} widersprüchliche Messwerte.")

    readings = list(by_key.values())
    save_meter_readings(db_path, readings)
    return readings, sources, issues


def _base_config(db_path: Path, runtime: RuntimeConfig, days: list[date]):
    """A LegConfig for labels/processing settings, built for the first day that has a tariff."""
    last_error: IncompleteConfigError | None = None
    for day in days:
        try:
            return build_leg_config(db_path, runtime, as_of=day)
        except IncompleteConfigError as exc:
            last_error = exc
    raise last_error or IncompleteConfigError("Keine Konfiguration für den Zeitraum.")


def compute_billing_preview(
    db_path: Path, runtime: RuntimeConfig, period_start: date, period_end: date,
) -> BillingPreview:
    """Scan available meter data and compute a live billing preview for a period.

    Per quarter hour, only meters eligible on that local day take part
    (meter validity, active participant, effective contract membership —
    see shareomat.database.billing_eligibility) and the tariff valid on that
    day applies, so joins, departures and price changes inside the period
    are respected. Everything that makes the result unreliable is collected
    in availability.blocking_issues; release is refused while any remain.

    Raises shareomat.database.config_builder.IncompleteConfigError if
    setup (community/participants/meters/tariff) is incomplete for the
    period, or BillingWorkflowError for an invalid period.
    """
    if period_end < period_start:
        raise BillingWorkflowError("Enddatum darf nicht vor dem Startdatum liegen.")

    days = [period_start + timedelta(days=i) for i in range((period_end - period_start).days + 1)]
    base_config = _base_config(db_path, runtime, days)
    # hard: never releasable (wrong or missing basis); soft: releasable only with a written reason.
    readings, sources, hard = _collect_readings_for_period(db_path, runtime, period_start, period_end)
    soft: list[str] = []
    resolver = EligibilityResolver(db_path)

    configured_meter_ids = {m.meter_id for m in list_meters(db_path)}
    meter_ids_with_data = sorted({r.meter_id for r in readings})

    # ── per-day eligibility and tariffs ──
    tariffs_by_day: dict[date, object] = {}
    eligible_by_day: dict[date, dict[str, str]] = {}
    excluded: dict[str, str] = {}
    unconfirmed: set[str] = set()
    days_without_tariff: list[date] = []
    for day in days:
        eligibility = resolver.for_day(day)
        eligible_by_day[day] = eligibility.meters
        unconfirmed |= eligibility.unconfirmed_participants
        tariff = get_tariff_for_date(db_path, day)
        tariffs_by_day[day] = tariff
        if tariff is None and eligibility.meters:
            days_without_tariff.append(day)

    billable: list[IntervalReading] = []
    present_slots: dict[str, set] = {}
    present_channels: dict[tuple[str, str], set] = {}
    for r in readings:
        day = _local_day(r.slot_start)
        if r.meter_id in eligible_by_day.get(day, {}) and tariffs_by_day.get(day) is not None:
            billable.append(r)
            present_slots.setdefault(r.meter_id, set()).add(r.slot_start)
            present_channels.setdefault((r.meter_id, r.direction), set()).add(r.slot_start)
        elif r.meter_id in configured_meter_ids:
            reason = resolver.for_day(day).excluded.get(r.meter_id, "an diesem Tag nicht abrechnungsberechtigt")
            excluded.setdefault(r.meter_id, reason)

    # ── quality gate ──
    # Completeness per CHANNEL: a consumer needs its import series, a producer its
    # export series, a prosumer/storage both — "no value" is never treated as 0 kWh.
    role_of = {m.meter_id: m.role for m in list_meters(db_path)}
    expected_slots: dict[str, int] = {}
    for day in days:
        if tariffs_by_day.get(day) is None:
            continue
        for meter_id in eligible_by_day[day]:
            expected_slots[meter_id] = expected_slots.get(meter_id, 0) + _slots_in_local_day(day)
    for meter_id, expected in sorted(expected_slots.items()):
        for direction in _REQUIRED_CHANNELS.get(role_of.get(meter_id, ""), ("import", "export")):
            present = len(present_channels.get((meter_id, direction), ()))
            label = "Bezug" if direction == "import" else "Einspeisung"
            if present == 0:
                hard.append(f"Messpunkt {meter_id}: keine Messwerte für {label} im ganzen Zeitraum.")
            elif present < expected:
                soft.append(f"Messpunkt {meter_id} ({label}): {expected - present} von {expected} Viertelstunden ohne Messwert.")
    if days_without_tariff:
        soft.append(
            f"Kein gültiger LEG-Preis für {len(days_without_tariff)} Tag(e) ab "
            f"{days_without_tariff[0].strftime('%d.%m.%Y')} — diese Tage wurden nicht abgerechnet."
        )
    unknown = sorted(set(meter_ids_with_data) - configured_meter_ids)
    if unknown:
        hard.append(f"Unbekannte Messpunkte in den Daten: {', '.join(unknown)}.")
    if resolver.contract_mode and resolver.community_confirmed_start is None:
        soft.append("Der vom Netzbetreiber bestätigte LEG-Start ist unter „Gemeinschaft“ nicht erfasst.")
    if unconfirmed:
        soft.append("Beitritt vom Netzbetreiber noch nicht bestätigt: " + ", ".join(sorted(unconfirmed)) + ".")
    storage_meters = sorted(m.meter_id for m in list_meters(db_path) if m.role == "storage" and m.meter_id in present_slots)
    if storage_meters:
        hard.append(
            "Speicher-Messpunkte (" + ", ".join(storage_meters) + "): die Mengenbegrenzung nach Art. 19h Abs. 4 "
            "StromVV wird von Shareomat noch nicht geprüft."
        )

    # ── settle per tariff (each day's own price) ──
    totals: dict[str, dict] = {}
    readings_by_tariff: dict[int, list[IntervalReading]] = {}
    tariff_obj: dict[int, object] = {}
    for r in billable:
        t = tariffs_by_day[_local_day(r.slot_start)]
        readings_by_tariff.setdefault(t.id, []).append(r)
        tariff_obj[t.id] = t

    meters_all = list_meters(db_path)
    participants_all = list_participants(db_path)
    start_dt, end_dt = _period_bounds(period_start, period_end)
    for tariff_id, group in readings_by_tariff.items():
        t = tariff_obj[tariff_id]
        config = replace(base_config, tariff=t, meters=meters_all, participants=participants_all)
        match_results = match_all(readings_to_slots(group))
        records = compute_billing(match_results, config, start_dt, end_dt, source_files=[x.filename for x in sources])
        for rec in records:
            acc = totals.setdefault(rec.participant_id, {
                "label": rec.label, "meter_ids": set(), "local_received": 0.0, "local_cost": 0.0,
                "grid_import": 0.0, "grid_cost": 0.0, "local_supplied": 0.0, "grid_export": 0.0, "payout": 0.0,
            })
            acc["meter_ids"].update(rec.meter_ids)
            acc["local_received"] += rec.local_received_kwh
            acc["local_cost"] += rec.local_cost_chf
            acc["grid_import"] += rec.grid_import_kwh
            acc["grid_cost"] += rec.grid_cost_chf
            acc["local_supplied"] += rec.local_supplied_kwh
            acc["grid_export"] += rec.grid_export_kwh
            acc["payout"] += rec.producer_payout_chf

    line_items: list[BillingLineItem] = []
    for pid in sorted(totals, key=lambda k: totals[k]["label"]):
        acc = totals[pid]
        local_amount = _round4(acc["local_cost"])
        producer_credit = _round4(acc["payout"])
        charge, credit = Decimal(0), Decimal(0)  # set by _allocate_cents below
        line_items.append(BillingLineItem(
            participant_id=pid,
            participant_label=acc["label"],
            meter_ids=sorted(acc["meter_ids"]),
            local_received_kwh=_round4(acc["local_received"]),
            local_amount_chf=local_amount,
            grid_import_kwh=_round4(acc["grid_import"]),
            grid_amount_chf=_round4(acc["grid_cost"]),
            local_supplied_kwh=_round4(acc["local_supplied"]),
            grid_export_kwh=_round4(acc["grid_export"]),
            producer_credit_chf=producer_credit,
            charge_chf=charge,
            credit_chf=credit,
            net_amount_chf=charge - credit,
        ))
    _allocate_cents(line_items)

    # Energy clearing: every LEG kWh received was supplied by someone in the same quarter hour.
    received_total = sum(acc["local_received"] for acc in totals.values())
    supplied_total = sum(acc["local_supplied"] for acc in totals.values())
    if abs(received_total - supplied_total) > 1e-6:
        hard.append(
            f"Energie-Clearing stimmt nicht: bezogen {received_total:.4f} kWh ≠ geliefert {supplied_total:.4f} kWh."
        )
    # Money clearing (contract prices): Bezugspreis = Produzentenvergütung + Verwaltungsgebühr, so consumers pay
    # exactly the producers' credits plus the agreed fee — any other margin has no contractual basis.
    if resolver.contract_mode:
        for t in tariff_obj.values():
            if t.local_rate_chf_kwh != t.feed_in_rate_chf_kwh + t.admin_fee_chf_kwh:
                hard.append(
                    f"Tarif „{t.name}“: Bezugspreis ≠ Produzentenvergütung + Verwaltungsgebühr — "
                    "Differenz ohne vertragliche Grundlage."
                )

    # Nothing to bill is never a successful billing (e.g. no signed member yet).
    if received_total <= 0 or supplied_total <= 0:
        hard.append(
            "Keine abrechenbaren LEG-Mengen im Zeitraum — kein wirksam beigetretener Bezüger/Produzent mit "
            "zeitgleichen Messwerten (siehe „Nicht abgerechnet“)."
        )

    reference_tariff = next((t for t in tariffs_by_day.values() if t is not None), base_config.tariff)
    availability = DataAvailability(
        meters_with_data=[m for m in meter_ids_with_data if m in configured_meter_ids],
        meters_missing_data=sorted(m for m in expected_slots if m not in present_slots),
        unknown_meter_ids=unknown,
        reading_count=len(readings),
        sources=sources,
        blocking_issues=hard + soft,
        hard_issues=hard,
        excluded=excluded,
    )
    tariff_names = sorted({t.name for t in tariff_obj.values()}) or [reference_tariff.name]
    return BillingPreview(
        period_start=period_start,
        period_end=period_end,
        availability=availability,
        tariff_name=" / ".join(tariff_names),
        local_rate_chf_kwh=reference_tariff.local_rate_chf_kwh,
        grid_rate_chf_kwh=reference_tariff.grid_rate_chf_kwh,
        feed_in_rate_chf_kwh=reference_tariff.feed_in_rate_chf_kwh,
        line_items=line_items,
    )


# ── Persistence ──────────────────────────────────────────────────────────────


_RUN_SELECT = """
    SELECT billing_runs.*, billing_periods.period_start AS bp_start,
           billing_periods.period_end AS bp_end, billing_periods.label AS bp_label
    FROM billing_runs
    JOIN billing_periods ON billing_periods.id = billing_runs.billing_period_id
"""


def _load_issues(raw: str) -> tuple[list[str], list[str]]:
    """(hard, soft) from the stored JSON; runs saved before the split stored a plain list (all soft)."""
    if not raw:
        return [], []
    data = json.loads(raw)
    if isinstance(data, list):
        return [], data
    return data.get("hard", []), data.get("soft", [])


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
        total_producer_credit_chf=Decimal(row["total_producer_credit_chf"]),
        quality_issues=_load_issues(row["quality_issues"])[0] + _load_issues(row["quality_issues"])[1],
        hard_issues=_load_issues(row["quality_issues"])[0],
        result_fingerprint=row["result_fingerprint"],
        release_override_reason=row["release_override_reason"],
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
                     total_producer_credit_chf, quality_issues, result_fingerprint, computed_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    period_id, version, BILLING_STATUS_DRAFT,
                    community_row["community_id"], community_row["name"], preview.tariff_name,
                    str(preview.local_rate_chf_kwh), str(preview.grid_rate_chf_kwh), str(preview.feed_in_rate_chf_kwh),
                    len(preview.line_items),
                    str(preview.total_local_kwh), str(preview.total_leg_amount_chf),
                    str(preview.total_grid_kwh), str(preview.total_grid_amount_chf),
                    str(preview.total_producer_credit_chf),
                    json.dumps({
                        "hard": preview.availability.hard_issues,
                        "soft": [i for i in preview.availability.blocking_issues
                                 if i not in preview.availability.hard_issues],
                    }, ensure_ascii=False),
                    _result_fingerprint(preview),
                    now, now, now,
                ),
            )
            run_id = cursor.lastrowid

            for item in preview.line_items:
                person = conn.execute(
                    "SELECT label, email, address_line, postal_code, city FROM participants WHERE participant_id = ?",
                    (item.participant_id,),
                ).fetchone()
                recipient = json.dumps(dict(person), ensure_ascii=False) if person else ""
                conn.execute(
                    """
                    INSERT INTO billing_records
                        (billing_run_id, participant_id, participant_label, meter_ids,
                         local_received_kwh, local_amount_chf, grid_import_kwh, grid_amount_chf,
                         local_supplied_kwh, grid_export_kwh, producer_credit_chf, charge_chf, credit_chf,
                         net_amount_chf, recipient_snapshot, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, item.participant_id, item.participant_label, ",".join(item.meter_ids),
                        str(item.local_received_kwh), str(item.local_amount_chf),
                        str(item.grid_import_kwh), str(item.grid_amount_chf),
                        str(item.local_supplied_kwh), str(item.grid_export_kwh),
                        str(item.producer_credit_chf), str(item.charge_chf), str(item.credit_chf),
                        str(item.net_amount_chf), recipient, now,
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


def release_billing_run(
    db_path: Path, run_id: int, *, override_reason: str = "", runtime: RuntimeConfig | None = None,
) -> BillingRun:
    """Release a draft — from this point its billing_records are permanently immutable.

    Hard issues (wrong or missing basis: nothing billable, unreadable or
    conflicting data, unknown meters, clearing errors, unsupported storage)
    can never be released. Soft issues (data gaps, missing grid-operator
    confirmations) need a written reason, stored on the run and in the
    audit log. With `runtime`, the period is recomputed first and the
    release refused if anything changed since the draft was saved
    (corrected meter data, membership, tariffs).
    """
    if runtime is not None:
        draft = get_billing_run(db_path, run_id).run
        if draft.status == BILLING_STATUS_DRAFT and draft.result_fingerprint:
            current = compute_billing_preview(db_path, runtime, draft.period_start, draft.period_end)
            if _result_fingerprint(current) != draft.result_fingerprint:
                raise BillingWorkflowError(
                    "Seit dem Speichern des Entwurfs haben sich Messdaten, Mitgliedschaften oder Tarife geändert — "
                    "bitte den Zeitraum neu berechnen und als neuen Entwurf speichern."
                )
    with connect(db_path) as conn:
        with conn:
            row = conn.execute("SELECT status, quality_issues FROM billing_runs WHERE id = ?", (run_id,)).fetchone()
            if row is None:
                raise BillingWorkflowError(f"Abrechnung {run_id} wurde nicht gefunden.")
            if row["status"] != BILLING_STATUS_DRAFT:
                raise BillingWorkflowError(
                    f"Nur Entwürfe können freigegeben werden (aktueller Status: {row['status']})."
                )
            hard_issues, soft_issues = _load_issues(row["quality_issues"])
            if hard_issues:
                raise BillingWorkflowError(
                    "Freigabe nicht möglich — nicht übersteuerbare Fehler: " + " ".join(hard_issues[:3])
                    + (" …" if len(hard_issues) > 3 else "")
                )
            issues = soft_issues
            reason = override_reason.strip()
            if issues and not reason:
                raise BillingWorkflowError(
                    f"Freigabe gesperrt — {len(issues)} offene Prüfpunkt(e): " + " ".join(issues[:3])
                    + (" …" if len(issues) > 3 else "")
                    + " Daten ergänzen und neu berechnen, oder mit schriftlicher Begründung freigeben."
                )
            now = now_iso()
            conn.execute(
                "UPDATE billing_runs SET status = ?, released_at = ?, release_override_reason = ?, updated_at = ? "
                "WHERE id = ?",
                (BILLING_STATUS_RELEASED, now, reason if issues else "", now, run_id),
            )
            record_event(conn, "billing_run", run_id, "released",
                         f"trotz {len(issues)} Prüfpunkt(en), Begründung: {reason}" if issues else "Prüfungen bestanden")
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
            issued = conn.execute(
                "SELECT COUNT(*) FROM billing_documents WHERE billing_run_id = ? AND status != 'cancelled'", (run_id,)
            ).fetchone()[0]
            if issued:
                raise BillingWorkflowError(
                    f"Zu dieser Abrechnung sind {issued} Rechnung(en)/Gutschrift(en) erstellt — "
                    "zuerst die Belege stornieren (nur ohne gebuchte Zahlungen möglich)."
                )
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
            producer_credit_chf=Decimal(r["producer_credit_chf"]),
            charge_chf=Decimal(r["charge_chf"]),
            credit_chf=Decimal(r["credit_chf"]),
            net_amount_chf=Decimal(r["net_amount_chf"]),
        )
        for r in record_rows
    ]
    sources = [
        BillingSourceFile(id=r["id"], filename=r["filename"], sha256=r["sha256"], origin=r["origin"])
        for r in source_rows
    ]
    return BillingRunDetail(run=_row_to_run(row), line_items=line_items, sources=sources)
