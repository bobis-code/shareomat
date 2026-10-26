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

    A participant has neither a type nor a validity window of its own:
    the role (Erzeuger/Verbraucher/Speicher) belongs to each meter (see
    shareomat.core.participant_roles), meter validity lives on Meter, and
    LEG membership (Beitritt/Austritt) on the contract assignment. active
    is an independent on/off switch a manager can flip.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Participant:
    """One billing participant in the LEG/ZEV community (house, flat, or tenant)."""

    participant_id: str
    label: str
    email: str = ""
    address_line: str = ""
    postal_code: str = ""
    city: str = ""
    active: bool = True
