# -*- coding: utf-8 -*-
"""
File: shareomat/database/sdat_overview.py

Purpose:
    Read-only month overview of the grid operator's official E66 data
    (SDAT, ADR-0008): per participant and per day, how much was drawn and
    fed in and how much of it was LEG energy. Shown on the "Messdaten" page
    so the operator sees every day what arrived, long before the monthly
    billing.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Uses load_official_values(), i.e. exactly the values the official
    billing would use (newest delivery per quarter hour, cancellations,
    no downgrade of plausibilised values). Writes nothing. Days are local
    days (Europe/Zurich), so a day has 92, 96 or 100 quarter hours.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.database.meters import list_meters
from shareomat.database.participants import list_participants
from shareomat.database.sdat_store import load_official_values

_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
_SLOT = timedelta(minutes=15)


@dataclass
class EnergyTotals:
    """kWh of one row: drawn (consumption) and fed in (production), each total and LEG part."""

    consumption_total: Decimal = Decimal(0)
    consumption_local: Decimal = Decimal(0)
    production_total: Decimal = Decimal(0)
    production_local: Decimal = Decimal(0)
    temporary: int = 0          # values marked 21 (vorläufig)
    estimated: int = 0          # values marked 56 (Ersatzwert)

    def add(self, direction: str, energy_type: str, value: Decimal, condition: str) -> None:
        if energy_type == "total":
            if direction == "consumption":
                self.consumption_total += value
            else:
                self.production_total += value
        elif energy_type == "local":
            if direction == "consumption":
                self.consumption_local += value
            else:
                self.production_local += value
        self.temporary += condition == "21"
        self.estimated += condition == "56"


@dataclass
class DayRow:
    day: date
    totals: EnergyTotals
    quarter_hours: int          # quarter hours of the local day (92/96/100)
    complete_points: int        # metering points with a total value (drawn or fed in) for every quarter hour
    points: int                 # metering points seen in the month


@dataclass
class PointRow:
    metering_point_id: str
    label: str                  # participant (meter label), or the bare id for an unknown point
    known: bool
    totals: EnergyTotals
    days_with_data: int


@dataclass
class MonthOverview:
    month: date                 # first day of the month
    community_id: str
    days: list[DayRow] = field(default_factory=list)
    points: list[PointRow] = field(default_factory=list)
    total: EnergyTotals = field(default_factory=EnergyTotals)
    files: int = 0              # documents whose values are in use

    @property
    def has_data(self) -> bool:
        return bool(self.points)


# ── Private helpers ─────────────────────────────────────────────────────────


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_TZ).astimezone(timezone.utc)


def _next_month(month: date) -> date:
    return date(month.year + (month.month == 12), month.month % 12 + 1, 1)


def _labels(db_path: Path) -> dict[str, str]:
    participants = {p.participant_id: p.label for p in list_participants(db_path)}
    labels = {}
    for meter in list_meters(db_path):
        owner = participants.get(meter.participant_id, meter.participant_id)
        labels[meter.meter_id.strip().upper()] = f"{owner} ({meter.label})" if meter.label else owner
    return labels


# ── Public functions ────────────────────────────────────────────────────────


def month_overview(db_path: Path, month: date) -> MonthOverview:
    """Official E66 values of one month, per day (whole LEG) and per metering point."""
    month = month.replace(day=1)
    start, end = _local_midnight(month), _local_midnight(_next_month(month))
    data = load_official_values(db_path, start, end, document_type="E66")
    overview = MonthOverview(month=month, community_id=data.community_id, files=len(data.sources))
    if not data.values:
        return overview

    labels = _labels(db_path)
    by_day: dict[date, EnergyTotals] = {}
    by_point: dict[str, EnergyTotals] = {}
    point_days: dict[str, set[date]] = {}
    total_slots: dict[tuple[str, date], set[datetime]] = {}
    for (point, direction, energy_type, slot), official in data.values.items():
        day = slot.astimezone(_TZ).date()
        by_day.setdefault(day, EnergyTotals()).add(direction, energy_type, official.value, official.condition)
        by_point.setdefault(point, EnergyTotals()).add(direction, energy_type, official.value, official.condition)
        overview.total.add(direction, energy_type, official.value, official.condition)
        point_days.setdefault(point, set()).add(day)
        if energy_type == "total":
            total_slots.setdefault((point, day), set()).add(slot)

    for point in sorted(by_point, key=lambda p: (labels.get(p.upper(), p), p)):
        known = point.upper() in labels
        overview.points.append(PointRow(
            metering_point_id=point, label=labels.get(point.upper(), point), known=known,
            totals=by_point[point], days_with_data=len(point_days[point]),
        ))
    for day in sorted(by_day):
        quarter_hours = int((_local_midnight(day + timedelta(days=1)) - _local_midnight(day)) / _SLOT)
        complete = sum(1 for point in by_point if len(total_slots.get((point, day), ())) == quarter_hours)
        overview.days.append(DayRow(day=day, totals=by_day[day], quarter_hours=quarter_hours,
                                    complete_points=complete, points=len(by_point)))
    return overview
