# -*- coding: utf-8 -*-
"""
File: shareomat/models/tariff.py

Purpose:
    Domain model for one settlement tariff (CHF per kWh rates), valid for
    a date range.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container — no business logic, no I/O.
    Rates are Decimal so database round-trips never lose precision to
    float rounding. The settlement core (shareomat.core.pipeline.leg_billing)
    converts to float once at the point of arithmetic — see config_builder.

    Unlike Participant/Meter, a tariff has no natural business key (several
    versions can share the same name), so `id` (the internal database row
    id) is carried on the model itself — the admin UI needs it to build
    edit/toggle links. It is None for a Tariff that has not been saved yet.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class Tariff:
    """Settlement tariff rates in CHF per kWh, valid for a date range."""

    local_rate_chf_kwh: Decimal
    grid_rate_chf_kwh: Decimal
    feed_in_rate_chf_kwh: Decimal
    name: str = "Standard"
    valid_from: date | None = None
    valid_until: date | None = None
    active: bool = True
    id: int | None = None
