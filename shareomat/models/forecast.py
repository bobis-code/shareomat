# -*- coding: utf-8 -*-
"""
File: shareomat/models/forecast.py

Purpose:
    Dataclass for a single computed short-term LEG demand forecast point.
    Kept separate from shareomat.models.external_data — this is Shareomat's
    own derived statistic, not a fetched external market price.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class ConsumptionForecastPoint:
    """One forecasted 15-minute demand slot, with its own quality/provenance."""

    scope: str              # "leg" (participant-level reserved for later)
    participant_id: str     # "" when scope == "leg"
    slot_start: datetime    # UTC, the forecasted future slot
    forecast_kwh: float | None   # None when quality == "insufficient_data"
    quality: str             # "ok" | "insufficient_data"
    method: str
    sample_count: int
    data_period_start: datetime | None
    data_period_end: datetime | None
    computed_at: datetime | None = None
