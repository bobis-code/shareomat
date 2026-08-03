# -*- coding: utf-8 -*-
"""Tests for the transparent statistical LEG demand forecast algorithm."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from shareomat.core.pipeline.consumption_forecast import (
    HALF_LIFE_DAYS,
    MIN_SAMPLES,
    compute_leg_demand_forecast,
)
from shareomat.models.meter_data import IntervalReading

_TZ = ZoneInfo("Europe/Zurich")


def _reading(slot_start: datetime, value: float, meter_id="M1", direction="import") -> IntervalReading:
    return IntervalReading(meter_id=meter_id, slot_start=slot_start, value_kwh=value, direction=direction)


def test_forecast_uses_weighted_average_of_matching_weekday_slot() -> None:
    """3 historical occurrences (meets MIN_SAMPLES) at the same weekday/time produce a weighted forecast."""
    now = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)  # aligned to a 15-min slot boundary
    readings = [
        _reading(now - timedelta(weeks=3), 1.0),
        _reading(now - timedelta(weeks=2), 2.0),
        _reading(now - timedelta(weeks=1), 3.0),
    ]

    points = compute_leg_demand_forecast(readings, now=now, horizon_days=1, tz=_TZ)
    first = points[0]

    assert first.slot_start == now
    assert first.quality == "ok"
    assert first.sample_count == 3
    # Recency-weighted -> closer to the most recent (3.0) than a plain average (2.0)
    assert 2.0 < first.forecast_kwh < 3.0

    w1 = 0.5 ** (7 / HALF_LIFE_DAYS)
    w2 = 0.5 ** (14 / HALF_LIFE_DAYS)
    w3 = 0.5 ** (21 / HALF_LIFE_DAYS)
    expected = (w1 * 3.0 + w2 * 2.0 + w3 * 1.0) / (w1 + w2 + w3)
    assert first.forecast_kwh == pytest.approx(expected, rel=1e-9)


def test_insufficient_history_yields_no_fabricated_value() -> None:
    """Fewer than MIN_SAMPLES matching occurrences -> insufficient_data, forecast_kwh is None."""
    now = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
    readings = [
        _reading(now - timedelta(weeks=1), 3.0),
        _reading(now - timedelta(weeks=2), 2.0),
    ]
    assert MIN_SAMPLES > 2  # sanity: the fixture must stay below the threshold

    points = compute_leg_demand_forecast(readings, now=now, horizon_days=1, tz=_TZ)
    first = points[0]

    assert first.quality == "insufficient_data"
    assert first.forecast_kwh is None
    assert first.sample_count == 2


def test_missing_weeks_do_not_crash_and_reduce_sample_count() -> None:
    """Gaps in history (e.g. a meter offline for a week) are simply absent, not treated as zero."""
    now = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
    readings = [
        _reading(now - timedelta(weeks=1), 3.0),
        # weeks_ago=2 deliberately missing
        _reading(now - timedelta(weeks=3), 1.0),
        _reading(now - timedelta(weeks=4), 1.5),
    ]
    points = compute_leg_demand_forecast(readings, now=now, horizon_days=1, tz=_TZ)
    assert points[0].sample_count == 3
    assert points[0].quality == "ok"


def test_multiple_meters_are_summed_per_slot() -> None:
    """LEG-wide demand sums import readings across all meters for the same historical slot."""
    now = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
    readings = []
    for weeks_ago in (1, 2, 3):
        t = now - timedelta(weeks=weeks_ago)
        readings.append(_reading(t, 1.0, meter_id="A"))
        readings.append(_reading(t, 2.0, meter_id="B"))
        readings.append(_reading(t, 0.5, meter_id="C", direction="export"))  # must be excluded

    points = compute_leg_demand_forecast(readings, now=now, horizon_days=1, tz=_TZ)
    # Each historical occurrence summed to 3.0 (A+B), export excluded -> forecast equals plain 3.0
    assert points[0].forecast_kwh == pytest.approx(3.0, rel=1e-9)


def test_horizon_slot_count_matches_15_minute_granularity() -> None:
    """horizon_days=7 from an exact slot boundary produces 7 * 96 fifteen-minute slots."""
    now = datetime(2027, 1, 4, 0, 0, tzinfo=timezone.utc)
    points = compute_leg_demand_forecast([], now=now, horizon_days=7, tz=_TZ)
    assert len(points) == 7 * 96


def test_dst_transition_bucketed_by_local_time_not_raw_utc_offset() -> None:
    """A historical reading from before a DST change must still land in the correct local-time bucket.

    Europe/Zurich falls back from CEST (+2) to CET (+1) on the last Sunday
    of October. Constructing "now" after that transition and historical
    occurrences via local-time subtraction (which zoneinfo re-resolves per
    date) means the UTC clock time differs by an hour across the boundary
    even though local wall-clock time (14:00) is identical every week —
    exactly the case that would break a naive UTC-only bucketing scheme.
    """
    now_local = datetime(2026, 11, 2, 14, 0, tzinfo=_TZ)  # after the 2026 DST fall-back
    now = now_local.astimezone(timezone.utc)

    readings = [
        _reading((now_local - timedelta(weeks=w)).astimezone(timezone.utc), float(w))
        for w in (1, 2, 3)  # week 3 falls before the DST transition (still CEST)
    ]

    points = compute_leg_demand_forecast(readings, now=now, horizon_days=1, tz=_TZ)
    first = points[0]

    assert first.slot_start == now
    assert first.quality == "ok"
    assert first.sample_count == 3  # all 3 correctly bucketed despite differing UTC offsets
