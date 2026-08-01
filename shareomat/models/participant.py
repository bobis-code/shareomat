# -*- coding: utf-8 -*-
"""
File: shareomat/models/participant.py

Purpose:
    Domain model for one billing participant in the LEG/ZEV community
    (house, flat, or tenant).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container — no business logic, no I/O.

    Identity model:
        participant_id — business/billing identity (house, flat, tenant)
        One participant may have one or more meters.

    valid_from/valid_until describe the participant's membership window
    (e.g. a tenant moving in/out mid-year); active is an independent
    on/off switch a manager can flip regardless of dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Participant:
    """One billing participant in the LEG/ZEV community (house, flat, or tenant)."""

    participant_id: str
    label: str
    participant_type: str  # PARTICIPANT_TYPE_* from shareomat.leg_const
    email: str = ""
    address_line: str = ""
    postal_code: str = ""
    city: str = ""
    valid_from: date | None = None
    valid_until: date | None = None
    active: bool = True
