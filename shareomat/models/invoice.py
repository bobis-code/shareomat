# -*- coding: utf-8 -*-
"""
File: shareomat/models/invoice.py

Purpose:
    Invoice-related domain model dataclasses for the Shareomat settlement pipeline.
    Contains per-slot match results and per-participant billing records.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data containers — no business logic, no I/O.
    All datetime values are UTC internally; conversion to
    Europe/Zurich happens only at report output stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class MatchResult:
    """Local solar energy distribution result for one 15-minute slot."""

    slot_start: datetime

    # Community totals
    total_export_kwh: float          # sum of all meter exports in this slot
    total_import_kwh: float          # sum of all meter imports in this slot
    local_shared_kwh: float          # min(total_export, total_import)
    unmatched_export_kwh: float      # max(0, total_export - local_shared) → grid feed-in
    unmatched_import_kwh: float      # max(0, total_import - local_shared) → grid draw

    # Per-meter: import side (who received from the LEG pool, who drew from grid)
    meter_local_received_kwh: dict[str, float] = field(default_factory=dict)
    meter_grid_import_kwh: dict[str, float] = field(default_factory=dict)

    # Per-meter: export side (who supplied the LEG pool, who fed excess to grid)
    meter_local_supplied_kwh: dict[str, float] = field(default_factory=dict)
    meter_grid_export_kwh: dict[str, float] = field(default_factory=dict)

    # Bilateral flows: flows[exp_id][imp_id] = scaled kWh allocated from exp to imp.
    # Empty dict means no cross-meter flows occurred in this slot (single-meter community).
    flows: dict[str, dict[str, float]] = field(default_factory=dict)


@dataclass
class BillingRecord:
    """Final energy settlement for one participant over a complete settlement period."""

    participant_id: str
    label: str
    meter_ids: list[str]
    period_start: datetime
    period_end: datetime

    # Export side (what this participant supplied)
    total_export_kwh: float          # total energy exported by this participant
    local_supplied_kwh: float        # share that went into the LEG pool
    grid_export_kwh: float           # share that was fed to the grid

    # Import side (what this participant consumed)
    total_import_kwh: float          # total energy imported by this participant
    local_received_kwh: float        # share received from the LEG pool
    grid_import_kwh: float           # share drawn from the public grid

    # Cost (only for consumption; feed-in credit is handled by the grid operator)
    local_rate_chf: float
    grid_rate_chf: float
    local_cost_chf: float
    grid_cost_chf: float
    total_cost_chf: float

    # Audit
    slot_count: int = 0
    source_files: list[str] = field(default_factory=list)
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
