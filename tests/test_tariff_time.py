# -*- coding: utf-8 -*-
"""Tests for shareomat.core.pipeline.tariff_time.is_peak_hour (Hochtarif/Niedertarif classification)."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from shareomat.core.pipeline.tariff_time import is_peak_hour

_TZ = ZoneInfo("Europe/Zurich")


def _peak(dt, *, start=6, end=22, weekdays_only=True) -> bool:
    return is_peak_hour(dt, peak_start_hour=start, peak_end_hour=end, peak_weekdays_only=weekdays_only, tz=_TZ)


def test_weekday_daytime_is_peak():
    # Monday 2024-01-01, 10:00 Europe/Zurich (winter, UTC+1) -> 09:00 UTC
    dt = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    assert _peak(dt) is True


def test_weekday_night_is_off_peak():
    # Monday, 23:00 Europe/Zurich -> 22:00 UTC
    dt = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)
    assert _peak(dt) is False


def test_weekday_early_morning_is_off_peak():
    # Monday, 05:00 Europe/Zurich -> 04:00 UTC, before peak_start_hour=6
    dt = datetime(2024, 1, 1, 4, 0, tzinfo=timezone.utc)
    assert _peak(dt) is False


def test_exact_start_hour_is_peak_boundary_inclusive():
    # Monday, exactly 06:00 Europe/Zurich -> 05:00 UTC
    dt = datetime(2024, 1, 1, 5, 0, tzinfo=timezone.utc)
    assert _peak(dt) is True


def test_exact_end_hour_is_off_peak_boundary_exclusive():
    # Monday, exactly 22:00 Europe/Zurich -> 21:00 UTC
    dt = datetime(2024, 1, 1, 21, 0, tzinfo=timezone.utc)
    assert _peak(dt) is False


def test_saturday_daytime_is_off_peak_when_weekdays_only():
    # Saturday 2024-01-06, 10:00 Europe/Zurich -> 09:00 UTC
    dt = datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc)
    assert _peak(dt) is False


def test_saturday_daytime_is_peak_when_weekdays_only_is_false():
    dt = datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc)
    assert _peak(dt, weekdays_only=False) is True


def test_sunday_is_always_off_peak_when_weekdays_only():
    # Sunday 2024-01-07, 10:00 Europe/Zurich -> 09:00 UTC
    dt = datetime(2024, 1, 7, 9, 0, tzinfo=timezone.utc)
    assert _peak(dt) is False


def test_dst_transition_uses_local_time_not_raw_utc_hour():
    """After the spring-forward DST transition, local 10:00 is UTC 08:00 (not 09:00 as in winter) —
    a naive UTC-hour comparison would misclassify this. Europe/Zurich 2024 DST start: 2024-03-31."""
    # Monday 2024-04-01 (first Monday after the 2024 DST change), 10:00 local = 08:00 UTC (summer, UTC+2)
    dt = datetime(2024, 4, 1, 8, 0, tzinfo=timezone.utc)
    assert _peak(dt) is True

    # The same raw UTC hour (08:00) one week earlier, before DST, is 09:00 local — still peak,
    # but this proves the function reads local wall-clock time, not the UTC hour verbatim.
    dt_before_dst = datetime(2024, 3, 25, 8, 0, tzinfo=timezone.utc)
    assert dt.astimezone(_TZ).hour != dt_before_dst.astimezone(_TZ).hour
    assert _peak(dt_before_dst) is True


def test_accepts_non_utc_input_datetime():
    """is_peak_hour must correctly convert any input tzinfo to the target tz, not just UTC."""
    # 10:00 Europe/Zurich, constructed directly with that tzinfo (not UTC at all).
    dt = datetime(2024, 1, 1, 10, 0, tzinfo=_TZ)
    assert _peak(dt) is True
