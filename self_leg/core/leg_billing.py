# -*- coding: utf-8 -*-
"""
File: self_leg/core/leg_billing.py

Purpose:
    Aggregates slot-level match results into per-participant billing
    records over a complete settlement period.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    No rounding is applied here. Full float precision is preserved.
    Rounding to CHF display precision happens in leg_report exclusively.

    Both sides are aggregated:
        Import side: local_received_kwh, grid_import_kwh (basis for cost)
        Export side: local_supplied_kwh, grid_export_kwh (audit trail)

    Cost is computed only for consumption (import side). Feed-in credit
    is handled directly between the participant and the grid operator.

    source_files and created_at are captured for legal traceability.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from self_leg.core.leg_config import LegConfig
from self_leg.models.invoice import BillingRecord, MatchResult

logger = logging.getLogger(__name__)


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

    # Import side totals
    totals_local_received: dict[str, float] = {}
    totals_grid_import: dict[str, float] = {}
    # Export side totals
    totals_local_supplied: dict[str, float] = {}
    totals_grid_export: dict[str, float] = {}

    for result in results:
        for meter_id, kwh in result.meter_local_received_kwh.items():
            pid = meter_to_participant.get(meter_id, meter_id)
            totals_local_received[pid] = totals_local_received.get(pid, 0.0) + kwh
        for meter_id, kwh in result.meter_grid_import_kwh.items():
            pid = meter_to_participant.get(meter_id, meter_id)
            totals_grid_import[pid] = totals_grid_import.get(pid, 0.0) + kwh
        for meter_id, kwh in result.meter_local_supplied_kwh.items():
            pid = meter_to_participant.get(meter_id, meter_id)
            totals_local_supplied[pid] = totals_local_supplied.get(pid, 0.0) + kwh
        for meter_id, kwh in result.meter_grid_export_kwh.items():
            pid = meter_to_participant.get(meter_id, meter_id)
            totals_grid_export[pid] = totals_grid_export.get(pid, 0.0) + kwh

    local_rate = config.tariffs.local_rate_chf_kwh
    grid_rate = config.tariffs.grid_rate_chf_kwh
    now = datetime.now(timezone.utc)

    all_pids = sorted(
        set(totals_local_received) | set(totals_grid_import) |
        set(totals_local_supplied) | set(totals_grid_export)
    )

    records: list[BillingRecord] = []
    for pid in all_pids:
        local_received = totals_local_received.get(pid, 0.0)
        grid_import = totals_grid_import.get(pid, 0.0)
        local_supplied = totals_local_supplied.get(pid, 0.0)
        grid_export = totals_grid_export.get(pid, 0.0)

        local_cost = local_received * local_rate
        grid_cost = grid_import * grid_rate

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
            local_rate_chf=local_rate,
            grid_rate_chf=grid_rate,
            local_cost_chf=local_cost,
            grid_cost_chf=grid_cost,
            total_cost_chf=local_cost + grid_cost,
            slot_count=len(results),
            source_files=list(source_files) if source_files else [],
            created_at=now,
        ))
        logger.debug(
            "Billing %s: supplied=%.4f kWh  received=%.4f kWh  cost=%.4f CHF",
            pid, local_supplied, local_received, local_cost + grid_cost,
        )

    logger.info("Computed %d billing record(s)", len(records))
    return records
