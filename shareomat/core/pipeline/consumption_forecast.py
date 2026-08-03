# -*- coding: utf-8 -*-
"""
File: shareomat/core/pipeline/consumption_forecast.py

Purpose:
    Transparent statistical short-term LEG demand forecast — no ML/AI.
    For each future 15-minute slot, averages historical LEG-wide
    consumption at the same local weekday + time-of-day, weighting more
    recent weeks higher. Produces "insufficient_data" (never a fabricated
    number) when too little matching history exists.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Weekday/time-of-day bucketing happens in LOCAL time (zoneinfo, not SQL
    strftime) so DST transitions don't shift which historical samples match
    a given target slot — same reasoning as leg_report.py/leg_normalizer.py.

    Known simplification: a meter with no reading for a historical slot
    simply contributes nothing to that slot's total (not an artificial
    zero) — a temporarily offline meter lowers that one historical
    occurrence's total, diluted by the other weighted weeks.

    Holiday handling is deliberately NOT implemented — there is no
    verified Swiss public-holiday data source in Shareomat yet. Adding one
    is a future enhancement, not a guess.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from shareomat.leg_const import SLOT_MINUTES
from shareomat.models.forecast import ConsumptionForecastPoint
from shareomat.models.meter_data import IntervalReading

METHOD = "weekday_time_weighted_recency_v1"
LOOKBACK_WEEKS = 8
HALF_LIFE_DAYS = 21
MIN_SAMPLES = 3

_BucketKey = tuple[int, tuple[int, int]]  # (local weekday, (hour, minute))


def _bucket_key(local_dt: datetime) -> _BucketKey:
    return (local_dt.weekday(), (local_dt.hour, local_dt.minute))


def compute_leg_demand_forecast(
    readings: list[IntervalReading],
    *, now: datetime, horizon_days: int, tz: ZoneInfo,
) -> list[ConsumptionForecastPoint]:
    """Forecast LEG-wide demand for each 15-min slot from now to now+horizon_days."""
    data_period_start = now - timedelta(weeks=LOOKBACK_WEEKS)
    data_period_end = now
    now_local_date = now.astimezone(tz).date()

    # LEG-wide total demand per historical slot (sum across meters).
    slot_totals: dict[datetime, float] = defaultdict(float)
    for r in readings:
        if r.direction != "import":
            continue
        if not (data_period_start <= r.slot_start < data_period_end):
            continue
        slot_totals[r.slot_start] += r.value_kwh

    # Bucket historical occurrences by (local weekday, local time-of-day),
    # keeping only genuinely past occurrences (age_days > 0).
    buckets: dict[_BucketKey, list[tuple[int, float]]] = defaultdict(list)
    for slot_start, total_kwh in slot_totals.items():
        local_dt = slot_start.astimezone(tz)
        age_days = (now_local_date - local_dt.date()).days
        if age_days <= 0:
            continue
        buckets[_bucket_key(local_dt)].append((age_days, total_kwh))

    slot_delta = timedelta(minutes=SLOT_MINUTES)
    horizon_start = now - timedelta(
        minutes=now.minute % SLOT_MINUTES, seconds=now.second, microseconds=now.microsecond,
    )
    horizon_end = now + timedelta(days=horizon_days)

    points: list[ConsumptionForecastPoint] = []
    target = horizon_start
    while target < horizon_end:
        occurrences = buckets.get(_bucket_key(target.astimezone(tz)), [])
        sample_count = len(occurrences)

        if sample_count >= MIN_SAMPLES:
            weighted_sum = sum(0.5 ** (age / HALF_LIFE_DAYS) * value for age, value in occurrences)
            weight_total = sum(0.5 ** (age / HALF_LIFE_DAYS) for age, _ in occurrences)
            forecast_kwh = weighted_sum / weight_total
            quality = "ok"
        else:
            forecast_kwh = None
            quality = "insufficient_data"

        points.append(ConsumptionForecastPoint(
            scope="leg", participant_id="", slot_start=target,
            forecast_kwh=forecast_kwh, quality=quality, method=METHOD,
            sample_count=sample_count,
            data_period_start=data_period_start, data_period_end=data_period_end,
        ))
        target += slot_delta

    return points
