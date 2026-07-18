# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_normalizer.py

Purpose:
    Normalization utilities shared across all raw provider parsers.
    Converts provider-specific raw rows into canonical IntervalReading objects.
    The canonical model starts here — everything downstream sees only IntervalReading.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    snap_to_slot() is used by both leg_parser.py (generic CSV/S-DAT) and
    all raw/ provider parsers to floor timestamps to 15-minute boundaries.

    EBL-specific normalization:
        - EBL timestamps are in Europe/Zurich local time (CET/CEST)
        - EBL uses end-of-interval convention (00:15 = interval 00:00–00:15)
        - normalize_ebl_row() corrects both before producing IntervalReading objects
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from shareomat.leg_const import (
    DIRECTION_EXPORT,
    DIRECTION_IMPORT,
    QUALITY_VALID,
    SLOT_MINUTES,
)
from shareomat.models.meter import IntervalReading

if TYPE_CHECKING:
    from shareomat.core.pipeline.raw.ebl_xlsx import EblRow

logger = logging.getLogger(__name__)

_TZ_ZURICH = ZoneInfo("Europe/Zurich")


# ── Shared utilities ──────────────────────────────────────────────────────────


def snap_to_slot(dt: datetime, slot_minutes: int) -> datetime:
    """Round a timezone-aware datetime down to the nearest slot boundary."""
    total = dt.hour * 60 + dt.minute
    snapped = (total // slot_minutes) * slot_minutes
    return dt.replace(hour=snapped // 60, minute=snapped % 60, second=0, microsecond=0)


# ── EBL normalization ─────────────────────────────────────────────────────────


def normalize_ebl_row(row: EblRow, slot_minutes: int = SLOT_MINUTES) -> list[IntervalReading]:
    """Convert one EBL raw row to canonical IntervalReading objects.

    Handles two EBL-specific conventions:
      - Timestamps are in Europe/Zurich local time (not UTC)
      - Timestamps mark the END of the interval, not the start
    """
    # Localize the naive EBL timestamp to Zürich, then subtract slot duration
    # to get slot_start, then convert to UTC for internal storage
    ts_zurich = row.timestamp_end.replace(tzinfo=_TZ_ZURICH)
    slot_start = snap_to_slot(
        (ts_zurich - timedelta(minutes=slot_minutes)).astimezone(timezone.utc),
        slot_minutes,
    )

    readings: list[IntervalReading] = []

    if row.bezug_kwh > 0:
        readings.append(IntervalReading(
            meter_id=row.meter_id,
            slot_start=slot_start,
            value_kwh=row.bezug_kwh,
            direction=DIRECTION_IMPORT,
            quality=QUALITY_VALID,
            source_file=row.source_file,
        ))

    if row.ruecklieferung_kwh > 0:
        readings.append(IntervalReading(
            meter_id=row.meter_id,
            slot_start=slot_start,
            value_kwh=row.ruecklieferung_kwh,
            direction=DIRECTION_EXPORT,
            quality=QUALITY_VALID,
            source_file=row.source_file,
        ))

    return readings
