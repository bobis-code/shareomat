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


@dataclass
class Community:
    """The LEG/ZEV community that participants, meters, and tariffs belong to."""

    community_id: str
    name: str
    address_line: str = ""
    postal_code: str = ""
    city: str = ""
    active: bool = True
