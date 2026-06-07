# -*- coding: utf-8 -*-
"""
File: self_leg/core/leg_matcher.py

Purpose:
    Proportional local energy sharing algorithm for Swiss LEG/ZEV
    settlement. Allocates locally produced energy to consumer meters
    on a pro-rata import basis for each 15-minute slot.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    No rounding is applied here. Full float precision is preserved
    throughout the matching pipeline. Rounding to display precision
    is the responsibility of leg_report exclusively.

    Both sides are tracked per slot:
        Import side: who received from the LEG pool, who drew from grid
        Export side: who supplied the LEG pool, who fed excess to grid

    Invariants guaranteed per slot:
        Σ meter_local_received_kwh = local_shared_kwh
        Σ meter_local_supplied_kwh = local_shared_kwh
        meter_local_received + meter_grid_import = consumer_import (per meter)
        meter_local_supplied + meter_grid_export = producer_export (per meter)
"""

from __future__ import annotations

import logging
from datetime import datetime

from self_leg.models.meter import EnergySlot
from self_leg.models.invoice import MatchResult

logger = logging.getLogger(__name__)


def match_slot(slot: EnergySlot) -> MatchResult:
    """Distribute locally produced solar energy across consumers for one 15-minute slot."""
    total_export = sum(slot.producer_export.values())
    total_import = sum(slot.consumer_import.values())
    local_shared = min(total_export, total_import)

    # ── Import side: how each consumer meter draws from local pool vs grid ────

    meter_local_received: dict[str, float] = {}
    meter_grid_import: dict[str, float] = {}

    if total_import > 0:
        for meter_id, imp in slot.consumer_import.items():
            received = (imp / total_import) * local_shared
            meter_local_received[meter_id] = received
            meter_grid_import[meter_id] = imp - received
    else:
        for meter_id, imp in slot.consumer_import.items():
            meter_local_received[meter_id] = 0.0
            meter_grid_import[meter_id] = imp

    # ── Export side: how each supplier meter feeds local pool vs grid ─────────

    meter_local_supplied: dict[str, float] = {}
    meter_grid_export: dict[str, float] = {}

    if total_export > 0:
        for meter_id, exp in slot.producer_export.items():
            supplied = (exp / total_export) * local_shared
            meter_local_supplied[meter_id] = supplied
            meter_grid_export[meter_id] = exp - supplied
    else:
        for meter_id, exp in slot.producer_export.items():
            meter_local_supplied[meter_id] = 0.0
            meter_grid_export[meter_id] = exp

    return MatchResult(
        slot_start=slot.slot_start,
        total_export_kwh=total_export,
        total_import_kwh=total_import,
        local_shared_kwh=local_shared,
        unmatched_export_kwh=max(0.0, total_export - total_import),
        unmatched_import_kwh=max(0.0, total_import - total_export),
        meter_local_received_kwh=meter_local_received,
        meter_grid_import_kwh=meter_grid_import,
        meter_local_supplied_kwh=meter_local_supplied,
        meter_grid_export_kwh=meter_grid_export,
    )


def match_all(slots: dict[datetime, EnergySlot]) -> list[MatchResult]:
    """Run the sharing algorithm over all 15-minute slots and return results sorted by time."""
    results = [match_slot(slot) for slot in slots.values()]
    results.sort(key=lambda r: r.slot_start)
    logger.info("Matched %d slot(s)", len(results))
    return results
