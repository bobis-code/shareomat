# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_matcher.py

Purpose:
    Proportional local energy sharing algorithm for Swiss LEG/ZEV
    settlement. Allocates locally produced energy to consumer meters
    on a pro-rata import basis for each 15-minute slot.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    No rounding is applied here. Full float precision is preserved
    throughout the matching pipeline. Rounding to display precision
    is the responsibility of leg_report exclusively.

    Allocation rule (Art. 19g Abs. 3 StromVV, VSE BD LEG-CH 2025 Kap. 6.1,
    identical to what the grid operator delivers as E66 LEGE/LEGR):
        P = sum of all exports, C = sum of all imports in the slot
        L = min(P, C)                       — energy shared inside the LEG
        received_j = import_j * L / C       — same key for every importer
        supplied_i = export_i * L / P       — same key for every exporter

    There is no self-exclusion: a prosumer meter with both import and
    export in the same 15-minute slot (allowed — import and export are
    not netted within a quarter hour, VSE Kap. 5 (1)) gets the same share
    as everyone else. Excluding its own export would diverge from the
    grid operator's allocation and therefore from its invoice.

    flows[exp][imp] = L * (export_exp / P) * (import_imp / C) is an
    internal clearing matrix for the ledger report only (who "sold" to
    whom on paper); it may contain exp == imp for prosumers and sums up
    exactly to supplied/received.

    Example (all in one 15-min slot):
        Meter 1: export 5, import 5
        Meter 2: export 3, import 1
        Meter 3: export 0, import 20
        → P = 8, C = 26, L = 8
        → every importer receives 8/26 ≈ 30.8 % of its import:
          Meter 1 ≈ 1.538, Meter 2 ≈ 0.308, Meter 3 ≈ 6.154
        → every exporter sells 8/8 = 100 % of its export

    Invariants guaranteed per slot:
        Σ meter_local_received_kwh  = local_shared_kwh
        Σ meter_local_supplied_kwh  = local_shared_kwh
        meter_local_received  + meter_grid_import  = consumer_import  (per meter)
        meter_local_supplied  + meter_grid_export  = producer_export  (per meter)
        local_shared = min(total_export, total_import)
"""

from __future__ import annotations

import logging
from datetime import datetime

from shareomat.models.meter_data import EnergySlot
from shareomat.models.billing import MatchResult

logger = logging.getLogger(__name__)


def match_slot(slot: EnergySlot) -> MatchResult:
    """Distribute locally produced energy across consumers for one 15-minute slot (pro rata, no self-exclusion)."""
    total_export = sum(slot.producer_export.values())
    total_import = sum(slot.consumer_import.values())
    local_shared = min(total_export, total_import)

    export_share = local_shared / total_export if total_export > 0 else 0.0
    import_share = local_shared / total_import if total_import > 0 else 0.0

    meter_local_supplied = {exp_id: exp_kwh * export_share for exp_id, exp_kwh in slot.producer_export.items()}
    meter_grid_export = {
        exp_id: exp_kwh - meter_local_supplied[exp_id] for exp_id, exp_kwh in slot.producer_export.items()
    }
    meter_local_received = {imp_id: imp_kwh * import_share for imp_id, imp_kwh in slot.consumer_import.items()}
    meter_grid_import = {
        imp_id: imp_kwh - meter_local_received[imp_id] for imp_id, imp_kwh in slot.consumer_import.items()
    }

    # Clearing matrix for the ledger report: exporter i's sold share split
    # over importers by their import share.
    scaled_flows: dict[str, dict[str, float]] = {}
    if local_shared > 0:
        for exp_id, supplied in meter_local_supplied.items():
            if supplied > 0:
                scaled_flows[exp_id] = {
                    imp_id: supplied * (imp_kwh / total_import)
                    for imp_id, imp_kwh in slot.consumer_import.items()
                    if imp_kwh > 0
                }

    return MatchResult(
        slot_start=slot.slot_start,
        total_export_kwh=total_export,
        total_import_kwh=total_import,
        local_shared_kwh=local_shared,
        unmatched_export_kwh=total_export - local_shared,
        unmatched_import_kwh=total_import - local_shared,
        meter_local_received_kwh=meter_local_received,
        meter_grid_import_kwh=meter_grid_import,
        meter_local_supplied_kwh=meter_local_supplied,
        meter_grid_export_kwh=meter_grid_export,
        flows=scaled_flows,
    )


def match_all(slots: dict[datetime, EnergySlot]) -> list[MatchResult]:
    """Run the sharing algorithm over all 15-minute slots and return results sorted by time."""
    results = [match_slot(slot) for slot in slots.values()]
    results.sort(key=lambda r: r.slot_start)
    logger.info("Matched %d slot(s)", len(results))
    return results
