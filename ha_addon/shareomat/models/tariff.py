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

    rate_mode selects between a single flat rate (local_rate_chf_kwh /
    feed_in_rate_chf_kwh apply to all hours) and Hochtarif/Niedertarif
    (HT/NT) time-of-day differentiation — not every grid operator/LEG uses
    HT/NT, so this is configurable per tariff version, not assumed. When
    rate_mode == "ht_nt", local_rate_chf_kwh/feed_in_rate_chf_kwh are the
    Hochtarif (peak) rates and local_rate_nt_chf_kwh/feed_in_rate_nt_chf_kwh
    are the Niedertarif (off-peak) rates. grid_rate_chf_kwh stays a single
    flat rate regardless of rate_mode — it is informational only (the grid
    operator bills grid consumption directly), not part of the LEG
    contract's own pricing. admin_fee_chf_kwh (§3.3 of the LEG contract,
    "Kostentragung Abrechnung") is always a single flat rate independent of
    rate_mode, per explicit decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

RATE_MODE_FLAT = "flat"
RATE_MODE_HT_NT = "ht_nt"


@dataclass
class Tariff:
    """Settlement tariff rates in CHF per kWh, valid for a date range."""

    local_rate_chf_kwh: Decimal
    grid_rate_chf_kwh: Decimal
    feed_in_rate_chf_kwh: Decimal
    admin_fee_chf_kwh: Decimal = Decimal("0")
    rate_mode: str = RATE_MODE_FLAT
    local_rate_nt_chf_kwh: Decimal | None = None
    feed_in_rate_nt_chf_kwh: Decimal | None = None
    name: str = "Standard"
    valid_from: date | None = None
    valid_until: date | None = None
    active: bool = True
    id: int | None = None
