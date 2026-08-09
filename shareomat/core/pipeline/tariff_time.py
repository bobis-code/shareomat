# -*- coding: utf-8 -*-
"""
File: shareomat/core/pipeline/tariff_time.py

Purpose:
    Hochtarif/Niedertarif (HT/NT, peak/off-peak) time classification for
    tariffs with rate_mode="ht_nt" (see shareomat.models.tariff). Used by
    shareomat.core.pipeline.leg_billing to split per-slot energy into HT/NT
    buckets before applying the corresponding rates.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The HT/NT window itself is NOT hardcoded — not every grid operator
    defines it the same way, and no verified EBL-specific definition was
    available at the time this was written. The window is read from
    shareomat.models.settings.OperationSettings (peak_start_hour/
    peak_end_hour/peak_weekdays_only), editable in the admin UI.

    Classification happens in LOCAL time (zoneinfo), not raw UTC — a slot's
    UTC hour shifts across DST transitions, but "is this Hochtarif" must
    follow the wall clock, same reasoning as leg_report.py/
    consumption_forecast.py, which already convert to local time before
    any weekday/time-of-day comparison.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def is_peak_hour(
    dt: datetime, *, peak_start_hour: int, peak_end_hour: int, peak_weekdays_only: bool, tz: ZoneInfo,
) -> bool:
    """Return True if `dt` (any timezone) falls into the configured Hochtarif window in local time."""
    local_dt = dt.astimezone(tz)
    if peak_weekdays_only and local_dt.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return peak_start_hour <= local_dt.hour < peak_end_hour
