# -*- coding: utf-8 -*-
"""Tests for raw interval-reading persistence (shareomat.database.meter_readings)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shareomat.database.meter_readings import (
    aggregate_meter_totals,
    list_meter_readings,
    save_meter_readings,
)
from shareomat.database.sqlite import init_db
from shareomat.models.meter_data import IntervalReading


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    return path


def _reading(meter_id="M1", minute=0, value=1.5, direction="import") -> IntervalReading:
    return IntervalReading(
        meter_id=meter_id,
        slot_start=datetime(2026, 7, 1, 0, minute, tzinfo=timezone.utc),
        value_kwh=value,
        direction=direction,
    )


def test_save_and_list_readings(db_path) -> None:
    readings = [_reading(minute=0), _reading(minute=15, value=2.0)]
    written = save_meter_readings(db_path, readings)
    assert written == 2

    stored = list_meter_readings(db_path)
    assert len(stored) == 2
    assert {r.value_kwh for r in stored} == {1.5, 2.0}


def test_save_meter_readings_empty_list_is_noop(db_path) -> None:
    assert save_meter_readings(db_path, []) == 0
    assert list_meter_readings(db_path) == []


def test_upsert_overwrites_same_slot_without_duplicating(db_path) -> None:
    save_meter_readings(db_path, [_reading(minute=0, value=1.0)])
    save_meter_readings(db_path, [_reading(minute=0, value=9.9)])  # re-parsed with corrected value

    stored = list_meter_readings(db_path)
    assert len(stored) == 1
    assert stored[0].value_kwh == 9.9


def test_different_direction_same_slot_is_a_separate_row(db_path) -> None:
    save_meter_readings(db_path, [
        _reading(minute=0, value=1.0, direction="import"),
        _reading(minute=0, value=0.5, direction="export"),
    ])
    assert len(list_meter_readings(db_path)) == 2


def test_list_meter_readings_filters_by_meter_and_time_range(db_path) -> None:
    save_meter_readings(db_path, [
        _reading(meter_id="A", minute=0),
        _reading(meter_id="B", minute=0),
        _reading(meter_id="A", minute=30),
    ])
    only_a = list_meter_readings(db_path, meter_ids=["A"])
    assert {r.meter_id for r in only_a} == {"A"}
    assert len(only_a) == 2

    start = datetime(2026, 7, 1, 0, 15, tzinfo=timezone.utc)
    later_only = list_meter_readings(db_path, start=start)
    assert len(later_only) == 1


def test_aggregate_meter_totals_sums_per_day_and_direction(db_path) -> None:
    save_meter_readings(db_path, [
        _reading(minute=0, value=1.0, direction="import"),
        _reading(minute=15, value=2.0, direction="import"),
        _reading(minute=30, value=0.5, direction="export"),
    ])
    start = datetime(2026, 6, 30, tzinfo=timezone.utc)
    end = datetime(2026, 7, 2, tzinfo=timezone.utc)
    totals = aggregate_meter_totals(db_path, None, start, end)

    by_direction = {t["direction"]: t["total_kwh"] for t in totals}
    assert by_direction["import"] == pytest.approx(3.0)
    assert by_direction["export"] == pytest.approx(0.5)
