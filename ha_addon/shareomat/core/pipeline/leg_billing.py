# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_billing.py

Purpose:
    Aggregates slot-level match results into per-participant billing
    records over a complete settlement period.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    No rounding is applied here. Full float precision is preserved.
    Rounding to CHF display precision happens in leg_report exclusively.

    Both sides are aggregated:
        Import side: local_received_kwh, grid_import_kwh (basis for cost)
        Export side: local_supplied_kwh, grid_export_kwh (basis for producer payout)

    Cost is computed for consumption (import side) AND, since the LEG
    contract feature, producer payout for locally-supplied energy
    (feed_in_rate_chf_kwh × local_supplied_kwh — see
    shareomat.models.billing.BillingRecord.producer_payout_chf). Grid
    feed-in outside the LEG is still handled directly between the
    participant and the grid operator, not here.

    When config.tariff.rate_mode == "ht_nt", local_received_kwh and
    local_supplied_kwh are additionally split by Hochtarif/Niedertarif
    (shareomat.core.pipeline.tariff_time.is_peak_hour(), using each slot's
    own timestamp) before rates are applied — grid-side kWh stay a single
    flat bucket regardless of rate_mode, since grid_rate_chf_kwh is
    informational only (the grid operator bills grid consumption
    directly), not part of the LEG contract's own pricing.

    source_files and created_at are captured for legal traceability.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from shareomat.config import LegConfig, ProcessingConfig
from shareomat.core.pipeline.tariff_time import is_peak_hour
from shareomat.models.billing import BillingRecord, MatchResult
from shareomat.models.tariff import RATE_MODE_HT_NT, Tariff

logger = logging.getLogger(__name__)

_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))


def _participant_label(config: LegConfig, participant_id: str) -> str:
    """Return the display label for a participant (e.g. 'Wohnung 3 OG')."""
    for p in config.participants:
        if p.participant_id == participant_id:
            return p.label
    return participant_id


def _participant_meter_ids(config: LegConfig, participant_id: str) -> list[str]:
    """Return the active meter IDs belonging to a participant."""
    return [m.meter_id for m in config.meters if m.participant_id == participant_id and m.active]


def compute_billing(
    results: list[MatchResult],
    config: LegConfig,
    period_start: datetime,
    period_end: datetime,
    source_files: list[str] | None = None,
) -> list[BillingRecord]:
    """Sum all 15-minute slot results into one settlement record per participant."""
    meter_to_participant = {
        m.meter_id: m.participant_id
        for m in config.meters
        if m.active
    }

    ht_nt = config.tariff.rate_mode == RATE_MODE_HT_NT

    # Import side totals — split HT/NT only when the tariff uses HT/NT rates.
    # In flat mode every slot counts as "peak" and the "_nt" dicts stay
    # empty, so local_received_ht+local_received_nt == today's single sum —
    # this keeps flat-mode behavior byte-for-byte identical to before.
    totals_local_received_ht: dict[str, float] = {}
    totals_local_received_nt: dict[str, float] = {}
    totals_grid_import: dict[str, float] = {}
    # Export side totals (same HT/NT split, used for producer payout)
    totals_local_supplied_ht: dict[str, float] = {}
    totals_local_supplied_nt: dict[str, float] = {}
    totals_grid_export: dict[str, float] = {}

    for result in results:
        peak = True
        if ht_nt:
            peak = is_peak_hour(
                result.slot_start,
                peak_start_hour=config.processing.peak_start_hour,
                peak_end_hour=config.processing.peak_end_hour,
                peak_weekdays_only=config.processing.peak_weekdays_only,
                tz=_TZ,
            )
        received_bucket = totals_local_received_ht if peak else totals_local_received_nt
        supplied_bucket = totals_local_supplied_ht if peak else totals_local_supplied_nt

        for meter_id, kwh in result.meter_local_received_kwh.items():
            pid = meter_to_participant.get(meter_id, meter_id)
            received_bucket[pid] = received_bucket.get(pid, 0.0) + kwh
        for meter_id, kwh in result.meter_grid_import_kwh.items():
            pid = meter_to_participant.get(meter_id, meter_id)
            totals_grid_import[pid] = totals_grid_import.get(pid, 0.0) + kwh
        for meter_id, kwh in result.meter_local_supplied_kwh.items():
            pid = meter_to_participant.get(meter_id, meter_id)
            supplied_bucket[pid] = supplied_bucket.get(pid, 0.0) + kwh
        for meter_id, kwh in result.meter_grid_export_kwh.items():
            pid = meter_to_participant.get(meter_id, meter_id)
            totals_grid_export[pid] = totals_grid_export.get(pid, 0.0) + kwh

    # Tariff rates are Decimal at rest (database round-trip precision); the
    # settlement math itself stays float, matching every other kWh/CHF value here.
    # NT rates fall back to the HT rate when unset (flat mode, or an HT/NT
    # tariff that only bothered to set the HT rate) — never a silent 0.
    local_rate_ht = float(config.tariff.local_rate_chf_kwh)
    local_rate_nt = (
        float(config.tariff.local_rate_nt_chf_kwh)
        if config.tariff.local_rate_nt_chf_kwh is not None else local_rate_ht
    )
    feed_in_rate_ht = float(config.tariff.feed_in_rate_chf_kwh)
    feed_in_rate_nt = (
        float(config.tariff.feed_in_rate_nt_chf_kwh)
        if config.tariff.feed_in_rate_nt_chf_kwh is not None else feed_in_rate_ht
    )
    grid_rate = float(config.tariff.grid_rate_chf_kwh)
    now = datetime.now(timezone.utc)

    all_pids = sorted(
        set(totals_local_received_ht) | set(totals_local_received_nt) | set(totals_grid_import) |
        set(totals_local_supplied_ht) | set(totals_local_supplied_nt) | set(totals_grid_export)
    )

    records: list[BillingRecord] = []
    for pid in all_pids:
        local_received_ht = totals_local_received_ht.get(pid, 0.0)
        local_received_nt = totals_local_received_nt.get(pid, 0.0)
        local_received = local_received_ht + local_received_nt
        grid_import = totals_grid_import.get(pid, 0.0)
        local_supplied_ht = totals_local_supplied_ht.get(pid, 0.0)
        local_supplied_nt = totals_local_supplied_nt.get(pid, 0.0)
        local_supplied = local_supplied_ht + local_supplied_nt
        grid_export = totals_grid_export.get(pid, 0.0)

        local_cost = local_received_ht * local_rate_ht + local_received_nt * local_rate_nt
        grid_cost = grid_import * grid_rate
        producer_payout = local_supplied_ht * feed_in_rate_ht + local_supplied_nt * feed_in_rate_nt

        records.append(BillingRecord(
            participant_id=pid,
            label=_participant_label(config, pid),
            meter_ids=_participant_meter_ids(config, pid),
            period_start=period_start,
            period_end=period_end,
            total_export_kwh=local_supplied + grid_export,
            local_supplied_kwh=local_supplied,
            grid_export_kwh=grid_export,
            total_import_kwh=local_received + grid_import,
            local_received_kwh=local_received,
            grid_import_kwh=grid_import,
            local_rate_chf=local_rate_ht,
            grid_rate_chf=grid_rate,
            local_cost_chf=local_cost,
            grid_cost_chf=grid_cost,
            total_cost_chf=local_cost + grid_cost,
            producer_payout_chf=producer_payout,
            slot_count=len(results),
            source_files=list(source_files) if source_files else [],
            created_at=now,
        ))
        logger.debug(
            "Billing %s: supplied=%.4f kWh  received=%.4f kWh  cost=%.4f CHF  payout=%.4f CHF",
            pid, local_supplied, local_received, local_cost + grid_cost, producer_payout,
        )

    logger.info("Computed %d billing record(s)", len(records))
    return records


def resolve_producer_rate_series(
    tariff: Tariff,
    processing: ProcessingConfig,
    *,
    start: datetime,
    horizon_hours: int,
    tz: ZoneInfo = _TZ,
    slot_minutes: int = 15,
) -> list[tuple[datetime, float]]:
    """Resolve the producer feed-in credit (CHF/kWh, `feed_in_rate_chf_kwh` -
    never `local_rate_chf_kwh`, which is the consumer price including
    `admin_fee_chf_kwh`) for every slot in a forward horizon.

    This is the single place the HT/NT split for feed_in_rate is evaluated
    (same `is_peak_hour()` call as compute_billing() above) - Shareomat owns
    the tariff/HT-NT rules, so Emsomat's Sparkplug adapter (LEG/FeedInPrice,
    see shareomat.sparkplug.coordinator_mapping) receives an already-resolved
    per-slot rate and never needs to reimplement this logic itself."""
    feed_in_ht = float(tariff.feed_in_rate_chf_kwh)
    feed_in_nt = (
        float(tariff.feed_in_rate_nt_chf_kwh)
        if tariff.feed_in_rate_nt_chf_kwh is not None else feed_in_ht
    )
    ht_nt = tariff.rate_mode == RATE_MODE_HT_NT

    slot_count = int(horizon_hours * 60 / slot_minutes)
    series: list[tuple[datetime, float]] = []
    for i in range(slot_count):
        slot_start = start + timedelta(minutes=i * slot_minutes)
        if ht_nt and not is_peak_hour(
            slot_start,
            peak_start_hour=processing.peak_start_hour,
            peak_end_hour=processing.peak_end_hour,
            peak_weekdays_only=processing.peak_weekdays_only,
            tz=tz,
        ):
            rate = feed_in_nt
        else:
            rate = feed_in_ht
        series.append((slot_start, rate))
    return series
