# -*- coding: utf-8 -*-
"""
File: tests/test_leg_matcher.py

Purpose:
    Unit tests for leg_matcher — verifies the Swiss LEG/ZEV
    proportional energy sharing algorithm and its invariants.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    No rounding is applied in the matcher, so invariants are
    tested with exact float arithmetic via pytest.approx.
    Both import side (received/grid_import) and export side
    (supplied/grid_export) are covered by invariant tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from self_leg.core.pipeline.leg_matcher import match_all, match_slot
from self_leg.models.meter import EnergySlot

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
