# -*- coding: utf-8 -*-
"""
File: shareomat/models/meter.py

Purpose:
    Domain model for one official grid/operator meter (master data),
    mapped to a participant.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container — no business logic, no I/O.
    Raw meter *readings* (IntervalReading, EnergySlot) and inbox file
    descriptors (ImportFile) live in shareomat.models.meter_data — this
    module holds only meter master data.

    Identity model:
        meter_id       — real technical meter ID from EBL/grid operator
        participant_id — business/billing identity the meter belongs to

    valid_from/valid_until describe the meter's assignment window; active
    is an independent on/off switch a manager can flip regardless of dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Meter:
    """One official grid/operator meter mapped to a participant."""

    meter_id: str
    participant_id: str
    label: str
    role: str  # METER_ROLE_* from shareomat.leg_const
    valid_from: date | None = None
    valid_until: date | None = None
    active: bool = True
