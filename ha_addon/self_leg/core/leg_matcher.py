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

    Core rule: a meter can never supply itself.
        exporter.meter_id != importer.meter_id for every flow.

    This matters when a prosumer meter has both export AND import
    in the same slot. Its export goes into the community pool and
    is distributed to ALL OTHER importers proportionally. Its import
    is covered proportionally from ALL OTHER exporters. The same
    physical electricity cannot flow out and back into the same meter.

    Algorithm per slot:
        1. For each exporter E with export X_E:
               distribute X_E proportionally among all importers
               EXCEPT E itself (by import share).
        2. Sum all cross-meter flows to get raw_supplied per exporter
               and raw_received per importer.
        3. Scale down uniformly if total demand < total raw supply
               (demand-limited case).
        4. local_shared = sum of all actual cross-meter flows.

    Example (all in one 15-min slot):
        Meter 1: export 5, import 5
        Meter 2: export 3, import 1
        Meter 3: export 0, import 20
        → total_export = 8, total_import = 26
        → local_shared = 8  (all export used locally, no grid feedin)
        → Meter 1 receives 0.6 from Meter 2 (not from itself)
        → Meter 3 receives 7.16 from Meters 1+2 proportionally

    Invariants guaranteed per slot:
        Σ meter_local_received_kwh  = local_shared_kwh
        Σ meter_local_supplied_kwh  = local_shared_kwh
        meter_local_received  + meter_grid_import  = consumer_import  (per meter)
        meter_local_supplied  + meter_grid_export  = producer_export  (per meter)
        local_shared ≤ min(total_export, total_import)
"""

from __future__ import annotations

import logging
from datetime import datetime

from self_leg.models.meter import EnergySlot
from self_leg.models.invoice import MatchResult

logger = logging.getLogger(__name__)


def match_slot(slot: EnergySlot) -> MatchResult:
    """Distribute locally produced energy across consumers for one 15-minute slot.

    A meter can never supply itself — all flows are cross-meter only.
    """
    total_export = sum(slot.producer_export.values())
    total_import = sum(slot.consumer_import.values())

    # Build cross-meter flows: flow[exp_id][imp_id] for exp_id != imp_id.
    # Each exporter distributes their full export proportionally among
    # all importers other than themselves.
    flow: dict[str, dict[str, float]] = {}
    for exp_id, exp_kwh in slot.producer_export.items():
        others = {
            imp_id: imp_kwh
            for imp_id, imp_kwh in slot.consumer_import.items()
            if imp_id != exp_id
        }
        total_others = sum(others.values())
        if total_others > 0:
            flow[exp_id] = {
                imp_id: exp_kwh * (imp_kwh / total_others)
                for imp_id, imp_kwh in others.items()
            }

    # Aggregate raw totals from flows
    raw_supplied: dict[str, float] = {exp_id: sum(targets.values()) for exp_id, targets in flow.items()}
    raw_received: dict[str, float] = {imp_id: 0.0 for imp_id in slot.consumer_import}
    for targets in flow.values():
        for imp_id, amount in targets.items():
            raw_received[imp_id] += amount

    total_raw = sum(raw_supplied.values())

    # Scale down uniformly if demand is the limiting factor
    scale = min(1.0, total_import / total_raw) if total_raw > 0 else 0.0
    local_shared = total_raw * scale

    # Apply scale and compute grid residuals
    meter_local_supplied: dict[str, float] = {}
    meter_grid_export: dict[str, float] = {}
    for exp_id, exp_kwh in slot.producer_export.items():
        supplied = raw_supplied.get(exp_id, 0.0) * scale
        meter_local_supplied[exp_id] = supplied
        meter_grid_export[exp_id] = exp_kwh - supplied

    meter_local_received: dict[str, float] = {}
    meter_grid_import: dict[str, float] = {}
    for imp_id, imp_kwh in slot.consumer_import.items():
        received = raw_received.get(imp_id, 0.0) * scale
        meter_local_received[imp_id] = received
        meter_grid_import[imp_id] = imp_kwh - received

    return MatchResult(
        slot_start=slot.slot_start,
        total_export_kwh=total_export,
        total_import_kwh=total_import,
        local_shared_kwh=local_shared,
        unmatched_export_kwh=max(0.0, total_export - local_shared),
        unmatched_import_kwh=max(0.0, total_import - local_shared),
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
