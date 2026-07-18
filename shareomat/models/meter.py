# -*- coding: utf-8 -*-
"""
File: shareomat/models/meter.py

Purpose:
    Meter-related domain model dataclasses for the Shareomat settlement pipeline.
    Contains raw interval readings, 15-minute energy slot containers, and
    inbox file descriptors.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data containers — no business logic, no I/O.
    All datetime values are UTC internally; conversion to
    Europe/Zurich happens only at report output stage.

    Identity model:
        meter_id      — real technical meter ID from EBL/grid operator
        participant_id — business/billing identity (one per house/flat/tenant)
        label         — human-readable display name

    Energy balance invariants (hold for every slot and for the full period):
        local_shared_kwh = min(total_export_kwh, total_import_kwh)
        unmatched_export_kwh = max(0, total_export_kwh - local_shared_kwh)
        unmatched_import_kwh = max(0, total_import_kwh - local_shared_kwh)
        Σ meter_local_supplied_kwh = Σ meter_local_received_kwh = local_shared_kwh
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class IntervalReading:
    """One raw energy value from a smart meter (import or export over 15 minutes)."""

    meter_id: str
    slot_start: datetime     # UTC, floored to slot boundary
    value_kwh: float
    direction: str           # export | import
    quality: str = "valid"
    source_file: str = ""


@dataclass
class EnergySlot:
    """All meter readings for one 15-minute measurement window, grouped by meter."""

    slot_start: datetime
    producer_export: dict[str, float] = field(default_factory=dict)   # meter_id -> kWh
    consumer_import: dict[str, float] = field(default_factory=dict)   # meter_id -> kWh


@dataclass
class ImportFile:
    """A file discovered in the inbox pending processing."""

    path: Path
    file_type: str  # csv | sdat | xlsx
    sha256: str
