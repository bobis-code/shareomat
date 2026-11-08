# -*- coding: utf-8 -*-
"""
File: shareomat/live/masterdata.py

Purpose:
    The ONLY module of the live layer that reads the master data
    (ADR-0007, Abschnitt 2 and 3.1). Read-only.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Which meters take part in the LEG on a day is decided by the billing
    eligibility rule itself (EligibilityResolver.for_day()), used read-only
    and not copied. No energy, price or matching function is called here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.database.billing_eligibility import EligibilityResolver
from shareomat.database.community import get_community
from shareomat.database.live_sources import list_live_sources
from shareomat.database.meters import list_meters
from shareomat.leg_const import METER_ROLE_GRID
from shareomat.models.live_source import LiveSource
from shareomat.models.meter import Meter


def local_day(moment: datetime) -> date:
    """The local calendar day of a moment, in the same time zone as billing."""
    return moment.astimezone(ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))).date()


@dataclass
class ExpectedMeters:
    """E(t): the meters taking part in the LEG on one local day."""

    day: date
    meters: dict[str, str] = field(default_factory=dict)      # meter_id -> participant_id
    excluded: dict[str, str] = field(default_factory=dict)    # meter_id -> reason
    unconfirmed_participants: set[str] = field(default_factory=set)


def load_expected(db_path: Path, moment: datetime) -> ExpectedMeters:
    day = local_day(moment)
    result = EligibilityResolver(db_path).for_day(day)
    return ExpectedMeters(day=day, meters=dict(result.meters), excluded=dict(result.excluded),
                          unconfirmed_participants=set(result.unconfirmed_participants))


def load_sources(db_path: Path) -> list[LiveSource]:
    return list_live_sources(db_path)


def load_meters(db_path: Path) -> list[Meter]:
    """Every meter that can have a live source (all except the grid role)."""
    return [m for m in list_meters(db_path) if m.role != METER_ROLE_GRID]


def community_id(db_path: Path) -> str:
    community = get_community(db_path)
    return community.community_id if community else ""
