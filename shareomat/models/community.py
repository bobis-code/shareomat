# -*- coding: utf-8 -*-
"""
File: shareomat/models/community.py

Purpose:
    Domain model for the LEG/ZEV community itself (the tenant that owns
    participants, meters, and tariffs).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container — no business logic, no I/O.
    Exactly one active community is expected per running instance; the
    schema allows more, but the settlement core only ever consumes one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class Community:
    """The LEG/ZEV community that participants, meters, and tariffs belong to."""

    community_id: str
    name: str
    address_line: str = ""
    postal_code: str = ""
    city: str = ""
    active: bool = True
    # Energy Identification Code issued by Swissgrid for the LEG representative
    # in the CEM role — the receiver address for SDAT E31/E66 messages from the
    # Datahub (docs/sdat_leg_import.md §9). Distinct from community_id. Empty
    # until registered; incoming SDAT messages are then rejected.
    eic_code: str = ""
    # Founding at the grid operator (Art. 19g Abs. 1 StromVV: formation must be
    # reported 3 months ahead). vnb_confirmed_start is the start date the grid
    # operator actually confirmed — the contract date alone proves nothing.
    vnb_registered_at: date | None = None
    vnb_confirmed_start: date | None = None
