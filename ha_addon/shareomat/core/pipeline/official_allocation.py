# -*- coding: utf-8 -*-
"""
File: shareomat/core/pipeline/official_allocation.py

Purpose:
    The settlement path of ADR-0008, since 0.6.34 the only one (ADR-0012):
    turns the grid operator's official E66 split (LEG energy / residual
    energy per metering point and quarter hour) into the MatchResult
    objects leg_billing settles - WITHOUT allocating anything again.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The grid operator allocates the locally produced energy proportionally
    (Art. 19g StromVV). Until 0.6.34 Shareomat also did that itself for raw
    meter data (CSV, Excel; leg_matcher, removed). The grid operator's
    allocation is authoritative for billing and is taken over as it is:

        meter_local_received[m]  = E66 LEG energy, consumption (LGZ-LEGE)
        meter_grid_import[m]     = E66 residual energy, consumption (LGZ-LEGR)
        meter_local_supplied[m]  = E66 LEG energy, production (EGZ-LEGE)
        meter_grid_export[m]     = E66 residual energy, production (EGZ-LEGR)

    compute_billing() and everything after it (tariffs, HT/NT, credits,
    invoices) stay exactly as they are; they only read these four mappings.
    No bilateral `flows` matrix is invented: the grid operator does not
    deliver one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from shareomat.models.billing import MatchResult
from shareomat.models.meter_data import IntervalReading


@dataclass(frozen=True)
class OfficialSplit:
    """Official LEG / residual energy of one meter channel in one quarter hour (kWh)."""

    local_kwh: float
    residual_kwh: float


# ── Public functions ──────────────────────────────────────────────────────────


def official_match_results(
    readings: list[IntervalReading],
    splits: dict[tuple[str, str, datetime], OfficialSplit],
) -> list[MatchResult]:
    """MatchResults for the billable total readings, filled from the official split.

    `readings` are the (already eligibility-filtered) measured totals; `splits`
    is keyed (meter_id, "import"|"export", slot_start). Every reading must have
    its split - the caller only passes complete channels, and a missing one is
    a programming error, not a value to assume.
    """
    by_slot: dict[datetime, MatchResult] = {}
    for reading in readings:
        split = splits[(reading.meter_id, reading.direction, reading.slot_start)]
        result = by_slot.get(reading.slot_start)
        if result is None:
            result = MatchResult(
                slot_start=reading.slot_start, total_export_kwh=0.0, total_import_kwh=0.0,
                local_shared_kwh=0.0, unmatched_export_kwh=0.0, unmatched_import_kwh=0.0,
            )
            by_slot[reading.slot_start] = result
        if reading.direction == "import":
            result.total_import_kwh += reading.value_kwh
            result.meter_local_received_kwh[reading.meter_id] = split.local_kwh
            result.meter_grid_import_kwh[reading.meter_id] = split.residual_kwh
            result.local_shared_kwh += split.local_kwh
            result.unmatched_import_kwh += split.residual_kwh
        else:
            result.total_export_kwh += reading.value_kwh
            result.meter_local_supplied_kwh[reading.meter_id] = split.local_kwh
            result.meter_grid_export_kwh[reading.meter_id] = split.residual_kwh
            result.unmatched_export_kwh += split.residual_kwh
    return [by_slot[slot] for slot in sorted(by_slot)]
