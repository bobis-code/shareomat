# -*- coding: utf-8 -*-
"""
File: tests/test_leg_matcher.py

Purpose:
    Unit tests for leg_matcher — verifies the Swiss LEG/ZEV
    proportional energy sharing algorithm and its invariants.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    No rounding is applied in the matcher, so invariants are
    tested with exact float arithmetic via pytest.approx.
    Both import side (received/grid_import) and export side
    (supplied/grid_export) are covered by invariant tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shareomat.core.pipeline.leg_matcher import match_all, match_slot
from shareomat.models.meter_data import EnergySlot

TS  = datetime(2024, 6, 1, 12,  0, 0, tzinfo=timezone.utc)
TS2 = datetime(2024, 6, 1, 12, 15, 0, tzinfo=timezone.utc)

METER_PROD = "CH_PRODUCER"
METER_A    = "CH_PART_A"
METER_B    = "CH_PART_B"


def _slot(prod: float, a: float, b: float, ts: datetime = TS) -> EnergySlot:
    return EnergySlot(
        slot_start=ts,
        producer_export={METER_PROD: prod} if prod > 0 else {},
        consumer_import={METER_A: a, METER_B: b},
    )


class TestMatchSlot:
    def test_excess_production_all_import_served_locally(self):
        """Production > consumption: every kWh import is covered locally; excess feeds grid."""
        result = match_slot(_slot(prod=1.0, a=0.3, b=0.2))

        assert result.total_export_kwh == pytest.approx(1.0)
        assert result.total_import_kwh == pytest.approx(0.5)
        assert result.local_shared_kwh == pytest.approx(0.5)
        assert result.unmatched_export_kwh == pytest.approx(0.5)
        assert result.unmatched_import_kwh == pytest.approx(0.0)

        assert result.meter_local_received_kwh[METER_A] == pytest.approx(0.3)
        assert result.meter_local_received_kwh[METER_B] == pytest.approx(0.2)
        assert result.meter_grid_import_kwh[METER_A] == pytest.approx(0.0)
        assert result.meter_grid_import_kwh[METER_B] == pytest.approx(0.0)

        assert result.meter_local_supplied_kwh[METER_PROD] == pytest.approx(0.5)
        assert result.meter_grid_export_kwh[METER_PROD] == pytest.approx(0.5)

    def test_shortfall_proportional_grid_draw(self):
        """Production < consumption: participants share locally pro-rata; shortfall from grid."""
        result = match_slot(_slot(prod=0.3, a=0.4, b=0.2))

        assert result.local_shared_kwh == pytest.approx(0.3)
        assert result.unmatched_export_kwh == pytest.approx(0.0)
        assert result.unmatched_import_kwh == pytest.approx(0.3)

        assert result.meter_local_received_kwh[METER_A] == pytest.approx(0.2)
        assert result.meter_local_received_kwh[METER_B] == pytest.approx(0.1)
        assert result.meter_grid_import_kwh[METER_A] == pytest.approx(0.2)
        assert result.meter_grid_import_kwh[METER_B] == pytest.approx(0.1)

        assert result.meter_local_supplied_kwh[METER_PROD] == pytest.approx(0.3)
        assert result.meter_grid_export_kwh[METER_PROD] == pytest.approx(0.0)

    def test_balanced_no_grid_interaction(self):
        """Production == consumption: no grid feed-in and no grid draw."""
        result = match_slot(_slot(prod=0.5, a=0.3, b=0.2))

        assert result.local_shared_kwh == pytest.approx(0.5)
        assert result.unmatched_export_kwh == pytest.approx(0.0)
        assert result.unmatched_import_kwh == pytest.approx(0.0)

        assert result.meter_local_supplied_kwh[METER_PROD] == pytest.approx(0.5)
        assert result.meter_grid_export_kwh[METER_PROD] == pytest.approx(0.0)

    def test_no_production_all_grid(self):
        """Zero production: participants draw entirely from the grid."""
        result = match_slot(_slot(prod=0.0, a=0.4, b=0.2))

        assert result.local_shared_kwh == pytest.approx(0.0)
        assert result.unmatched_import_kwh == pytest.approx(0.6)
        assert result.meter_local_received_kwh[METER_A] == pytest.approx(0.0)
        assert result.meter_grid_import_kwh[METER_A] == pytest.approx(0.4)
        assert result.meter_grid_import_kwh[METER_B] == pytest.approx(0.2)

    def test_no_consumption_all_feed_in(self):
        """Zero consumption: all production feeds the grid."""
        slot = EnergySlot(
            slot_start=TS,
            producer_export={METER_PROD: 0.5},
            consumer_import={},
        )
        result = match_slot(slot)

        assert result.local_shared_kwh == pytest.approx(0.0)
        assert result.unmatched_export_kwh == pytest.approx(0.5)
        assert result.unmatched_import_kwh == pytest.approx(0.0)
        assert result.meter_local_supplied_kwh[METER_PROD] == pytest.approx(0.0)
        assert result.meter_grid_export_kwh[METER_PROD] == pytest.approx(0.5)

    def test_three_meter_proportional_split(self):
        """Three consumer meters each receive a proportional local share."""
        METER_C = "CH_PART_C"
        slot = EnergySlot(
            slot_start=TS,
            producer_export={METER_PROD: 0.3},
            consumer_import={METER_A: 0.2, METER_B: 0.5, METER_C: 0.3},
        )
        result = match_slot(slot)
        total_import = 1.0

        assert result.meter_local_received_kwh[METER_A] == pytest.approx(0.3 * (0.2 / total_import))
        assert result.meter_local_received_kwh[METER_B] == pytest.approx(0.3 * (0.5 / total_import))
        assert result.meter_local_received_kwh[METER_C] == pytest.approx(0.3 * (0.3 / total_import))

    def test_invariant_received_plus_grid_import_equals_total_import(self):
        """For every consumer meter: local_received + grid_import == original import."""
        slot = _slot(prod=0.25, a=0.3, b=0.4)
        result = match_slot(slot)

        for meter_id, original in slot.consumer_import.items():
            total = result.meter_local_received_kwh[meter_id] + result.meter_grid_import_kwh[meter_id]
            assert total == pytest.approx(original), f"import invariant failed for {meter_id}"

    def test_invariant_supplied_plus_grid_export_equals_total_export(self):
        """For every supplier meter: local_supplied + grid_export == original export."""
        slot = _slot(prod=0.25, a=0.3, b=0.4)
        result = match_slot(slot)

        for meter_id, original in slot.producer_export.items():
            total = result.meter_local_supplied_kwh[meter_id] + result.meter_grid_export_kwh[meter_id]
            assert total == pytest.approx(original), f"export invariant failed for {meter_id}"

    def test_invariant_sum_of_received_equals_local_shared(self):
        """Sum of all local_received must equal local_shared_kwh."""
        result = match_slot(_slot(prod=0.25, a=0.3, b=0.4))
        assert sum(result.meter_local_received_kwh.values()) == pytest.approx(result.local_shared_kwh)

    def test_invariant_sum_of_supplied_equals_local_shared(self):
        """Sum of all local_supplied must equal local_shared_kwh."""
        result = match_slot(_slot(prod=0.25, a=0.3, b=0.4))
        assert sum(result.meter_local_supplied_kwh.values()) == pytest.approx(result.local_shared_kwh)

    def test_prosumer_with_import_and_export_gets_same_key_as_everyone(self):
        """No self-exclusion: same pro-rata key for every importer (Art. 19g StromVV, EBL E66 LEGE)."""
        slot = EnergySlot(
            slot_start=TS,
            producer_export={"M1": 5.0, "M2": 3.0},
            consumer_import={"M1": 5.0, "M2": 1.0, "M3": 20.0},
        )
        result = match_slot(slot)

        assert result.local_shared_kwh == pytest.approx(8.0)
        assert result.meter_local_received_kwh["M1"] == pytest.approx(5.0 * 8 / 26)
        assert result.meter_local_received_kwh["M2"] == pytest.approx(1.0 * 8 / 26)
        assert result.meter_local_received_kwh["M3"] == pytest.approx(20.0 * 8 / 26)
        assert result.meter_local_supplied_kwh["M1"] == pytest.approx(5.0)
        assert result.meter_local_supplied_kwh["M2"] == pytest.approx(3.0)
        assert result.meter_grid_export_kwh["M1"] == pytest.approx(0.0)

    def test_spec_example_a_two_producers_three_consumers(self):
        """LEG_ONE_Mehrparteien §5: P=12, C=8 → P1 sells 6, P2 sells 2, every consumer fully covered."""
        slot = EnergySlot(
            slot_start=TS,
            producer_export={"P1": 9.0, "P2": 3.0},
            consumer_import={"C1": 4.0, "C2": 3.0, "C3": 1.0},
        )
        result = match_slot(slot)

        assert result.local_shared_kwh == pytest.approx(8.0)
        assert result.meter_local_supplied_kwh["P1"] == pytest.approx(6.0)
        assert result.meter_local_supplied_kwh["P2"] == pytest.approx(2.0)
        assert result.meter_grid_export_kwh["P1"] == pytest.approx(3.0)
        assert result.meter_grid_export_kwh["P2"] == pytest.approx(1.0)
        assert result.meter_local_received_kwh == pytest.approx({"C1": 4.0, "C2": 3.0, "C3": 1.0})
        # Clearing matrix (§5.2)
        assert result.flows["P1"] == pytest.approx({"C1": 3.0, "C2": 2.25, "C3": 0.75})
        assert result.flows["P2"] == pytest.approx({"C1": 1.0, "C2": 0.75, "C3": 0.25})

    def test_spec_example_b_shortfall(self):
        """LEG_ONE_Mehrparteien §6: P=5, C=10 → every consumer 50 %, clearing P1→C1 1.8 etc."""
        slot = EnergySlot(
            slot_start=TS,
            producer_export={"P1": 3.0, "P2": 2.0},
            consumer_import={"C1": 6.0, "C2": 4.0},
        )
        result = match_slot(slot)

        assert result.meter_local_received_kwh == pytest.approx({"C1": 3.0, "C2": 2.0})
        assert result.meter_grid_import_kwh == pytest.approx({"C1": 3.0, "C2": 2.0})
        assert result.flows["P1"] == pytest.approx({"C1": 1.8, "C2": 1.2})
        assert result.flows["P2"] == pytest.approx({"C1": 1.2, "C2": 0.8})

    def test_flows_sum_to_supplied_and_received(self):
        """Clearing matrix rows sum to supplied, columns to received — also with a prosumer."""
        slot = EnergySlot(
            slot_start=TS,
            producer_export={"M1": 5.0, "M2": 3.0},
            consumer_import={"M1": 5.0, "M2": 1.0, "M3": 20.0},
        )
        result = match_slot(slot)

        for exp_id, targets in result.flows.items():
            assert sum(targets.values()) == pytest.approx(result.meter_local_supplied_kwh[exp_id])
        for imp_id, received in result.meter_local_received_kwh.items():
            assert sum(t.get(imp_id, 0.0) for t in result.flows.values()) == pytest.approx(received)

    def test_empty_slot(self):
        """Empty slot produces all-zero result without error."""
        result = match_slot(EnergySlot(slot_start=TS))

        assert result.total_export_kwh == pytest.approx(0.0)
        assert result.total_import_kwh == pytest.approx(0.0)
        assert result.local_shared_kwh == pytest.approx(0.0)


class TestMatchAll:
    def test_results_sorted_ascending(self):
        """match_all returns results sorted by slot_start."""
        slots = {
            TS2: _slot(prod=0.5, a=0.3, b=0.2, ts=TS2),
            TS:  _slot(prod=0.5, a=0.3, b=0.2, ts=TS),
        }
        results = match_all(slots)

        assert len(results) == 2
        assert results[0].slot_start == TS
        assert results[1].slot_start == TS2

    def test_empty_input(self):
        assert match_all({}) == []
