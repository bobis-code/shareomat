# -*- coding: utf-8 -*-
"""
File: shareomat/database/sdat_analysis.py

Purpose:
    Read-only analysis of the grid operator's official E66 data (SDAT,
    ADR-0008) for "Energie & Analyse → Analyse":

    * period_sums():     per metering point and period (day, week, month,
                         year, free): drawn and fed in, each total / LEG /
                         grid, completeness and the check total = LEG + grid;
    * point_values():    the values of one metering point per quarter hour,
                         hour or day, with a row for every quarter hour that
                         must exist (gaps are shown, not skipped);
    * quarter_hour_origin(): every delivery of one quarter hour and which
                         value counts.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Everything is based on load_official_values(), i.e. exactly the values
    the official billing would use (newest delivery per quarter hour,
    cancellations, no downgrade of plausibilised values). Writes nothing.
    Days are local days (Europe/Zurich): 92, 96 or 100 quarter hours.

    Data is expected for a metering point only on days it belongs to the
    LEG: its participant's membership covers the day (joined, not left, not
    before the grid operator's effective start), the day is not before the
    LEG start the grid operator confirmed (Gemeinschaft) and the meter is
    valid then -
    the same rule as the billing (billing_eligibility). A participant who
    joins later is not "incomplete" before that day.

    The check total = LEG + grid uses the same resolution as the billing
    (SDAT_RESOLUTION_KWH): a difference up to 0.001 kWh is rounding.

    Speed: this reads the deliveries on every call. Fine for the LEG of
    today; for ~300 metering points over a year the derived day table of the
    concept (docs/development/Auswertung_EBL-Messdaten_Konzept.md, ADR-0010
    planned) replaces the inside of period_sums() without changing its result.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.database.community import get_community
from shareomat.database.memberships import list_memberships
from shareomat.database.meters import list_meters
from shareomat.database.participants import list_participants
from shareomat.database.sdat_store import (
    SDAT_RESOLUTION_KWH,
    deliveries_of_quarter_hour,
    load_official_values,
    official_data_end,
)
from shareomat.leg_const import METER_ROLE_GRID

_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
_SLOT = timedelta(minutes=15)

DIRECTIONS = ("consumption", "production")
ENERGY_TYPES = ("total", "local", "residual")
RESOLUTIONS = ("15min", "hour", "day")
# Longest period per call (the page refuses more; bigger periods use a coarser resolution).
MAX_DAYS = {"15min": 31, "hour": 92, "day": 366}
MAX_SUM_DAYS = 366
_MONTH_ROWS_FROM_DAYS = 63            # longer periods are broken down per month instead of per day
_ZONE_NAMES = {"CET": "MEZ", "CEST": "MESZ"}


@dataclass
class Flows:
    """kWh: drawn (consumption) and fed in (production), each total, LEG part and grid part (residual)."""

    consumption_total: Decimal = Decimal(0)
    consumption_local: Decimal = Decimal(0)
    consumption_residual: Decimal = Decimal(0)
    production_total: Decimal = Decimal(0)
    production_local: Decimal = Decimal(0)
    production_residual: Decimal = Decimal(0)
    temporary: int = 0          # values marked 21 (vorläufig)
    estimated: int = 0          # values marked 56 (Ersatzwert)

    def add(self, direction: str, energy_type: str, value: Decimal, condition: str) -> None:
        name = f"{direction}_{energy_type}"
        if direction in DIRECTIONS and energy_type in ENERGY_TYPES:
            setattr(self, name, getattr(self, name) + value)
        self.temporary += condition == "21"
        self.estimated += condition == "56"

    def merge(self, other: Flows) -> None:
        for direction in DIRECTIONS:
            for energy_type in ENERGY_TYPES:
                name = f"{direction}_{energy_type}"
                setattr(self, name, getattr(self, name) + getattr(other, name))
        self.temporary += other.temporary
        self.estimated += other.estimated


@dataclass
class Check:
    """Quality of a set of quarter hours of one or more metering points."""

    expected: int = 0           # quarter hours that must have values (up to the latest delivery)
    complete: int = 0           # of these: every series of the point delivered
    mismatched: int = 0         # total differs from LEG + grid by more than the rounding
    rounded: int = 0            # total differs from LEG + grid by the rounding only

    @property
    def missing(self) -> int:
        return max(self.expected - self.complete, 0)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.mismatched

    def merge(self, other: Check) -> None:
        self.expected += other.expected
        self.complete += other.complete
        self.mismatched += other.mismatched
        self.rounded += other.rounded


@dataclass(frozen=True)
class MeterChoice:
    metering_point_id: str
    meter_label: str
    participant: str
    active: bool
    role: str

    @property
    def title(self) -> str:
        """For drop-downs: "Muster Erika – Wohnung (…0001)"."""
        name = f"{self.participant} – {self.meter_label}" if self.meter_label else self.participant
        return f"{name} (…{self.metering_point_id[-4:]})"


@dataclass
class PointSums:
    metering_point_id: str
    meter_label: str
    participant: str
    known: bool                 # False: in the operator's data but not in the master data
    flows: Flows = field(default_factory=Flows)
    check: Check = field(default_factory=Check)


@dataclass
class PeriodRow:
    """One day (or month) of the selection."""

    start: date
    end: date                   # exclusive
    flows: Flows = field(default_factory=Flows)
    check: Check = field(default_factory=Check)
    points: int = 0                 # metering points of the selection
    incomplete_points: int = 0      # of these: at least one quarter hour missing in this row


@dataclass
class PeriodSums:
    start: date
    end: date                   # exclusive
    points: list[PointSums] = field(default_factory=list)
    total: Flows = field(default_factory=Flows)
    total_check: Check = field(default_factory=Check)
    rows: list[PeriodRow] = field(default_factory=list)
    row_unit: str = "day"       # "day" | "month"
    files: int = 0
    data_until: date | None = None      # last local day covered by any delivery

    @property
    def has_data(self) -> bool:
        return bool(self.points)


@dataclass
class ValueRow:
    start: datetime             # local time
    end: datetime               # local time
    flows: Flows = field(default_factory=Flows)
    check: Check = field(default_factory=Check)
    has_values: bool = False    # at least one value of the shown directions delivered
    member: bool = True         # False: the participant was not in the LEG then (nothing expected)
    delivered: bool = True      # False: after the latest delivery ("noch nicht geliefert")
    zone: str = ""              # "MEZ"/"MESZ" on days with a clock change, else ""

    @property
    def slot_key(self) -> str:
        """UTC ISO of the quarter hour (for the origin view)."""
        return self.start.astimezone(timezone.utc).isoformat()


@dataclass
class PointValues:
    metering_point_id: str
    start: date
    end: date                   # exclusive
    resolution: str
    directions: tuple[str, ...]
    rows: list[ValueRow] = field(default_factory=list)
    total: Flows = field(default_factory=Flows)
    total_check: Check = field(default_factory=Check)
    files: int = 0
    data_until: date | None = None


@dataclass
class OriginRow:
    file_name: str
    created_at: datetime | None          # local time
    cancellation: bool
    values: dict[tuple[str, str], tuple[Decimal, str]]
    in_use: set[tuple[str, str]] = field(default_factory=set)
    changed: set[tuple[str, str]] = field(default_factory=set)   # differs from the delivery before


@dataclass
class QuarterHourOrigin:
    metering_point_id: str
    start: datetime             # local time
    end: datetime               # local time
    effective: dict[tuple[str, str], tuple[Decimal, str]]
    deliveries: list[OriginRow]


# ── Private helpers ─────────────────────────────────────────────────────────


def _local_midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=_TZ).astimezone(timezone.utc)


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def _slots(start: datetime, end: datetime):
    slot = start
    while slot < end:
        yield slot
        slot += _SLOT


def _quarter_hours_of_day(day: date) -> int:
    return int((_local_midnight(day + timedelta(days=1)) - _local_midnight(day)) / _SLOT)


def _zone(moment_utc: datetime) -> str:
    local = moment_utc.astimezone(_TZ)
    if _quarter_hours_of_day(local.date()) == 96:
        return ""
    name = local.tzname() or ""
    return _ZONE_NAMES.get(name, name)


def _meter_index(db_path: Path) -> dict[str, MeterChoice]:
    participants = {p.participant_id: p.label for p in list_participants(db_path)}
    return {
        meter.meter_id.strip().upper(): MeterChoice(
            metering_point_id=meter.meter_id, meter_label=meter.label,
            participant=participants.get(meter.participant_id, meter.participant_id), active=meter.active,
            role=meter.role)
        for meter in list_meters(db_path)
    }


def _in_leg_rules(db_path: Path):
    """in_leg(point, day) -> bool: data of this metering point is expected on this local day.

    Points outside the master data are never expected (they only show up with data).
    """
    memberships: dict[str, list] = {}
    for membership in list_memberships(db_path):
        memberships.setdefault(membership.participant_id, []).append(membership)
    meters = {meter.meter_id.strip().upper(): meter for meter in list_meters(db_path)}
    community = get_community(db_path)
    leg_start = community.vnb_confirmed_start if community else None     # same hard lower bound as billing
    cache: dict[tuple[str, date], bool] = {}

    def in_leg(point: str, day: date) -> bool:
        key = (point, day)
        if key not in cache:
            meter = meters.get(point)
            valid = (meter is not None and (meter.valid_from is None or day >= meter.valid_from)
                     and (meter.valid_until is None or day <= meter.valid_until))
            cache[key] = valid and (leg_start is None or day >= leg_start) and any(
                m.covers(day) and (m.effective_start_at is None or day >= m.effective_start_at)
                for m in memberships.get(meter.participant_id, []))
        return cache[key]
    return in_leg


def _by_point_and_slot(values) -> dict[str, dict[datetime, dict[tuple[str, str], object]]]:
    grouped: dict[str, dict[datetime, dict[tuple[str, str], object]]] = {}
    for (point, direction, energy_type, slot), official in values.items():
        grouped.setdefault(point.strip().upper(), {}).setdefault(slot, {})[(direction, energy_type)] = official
    return grouped


def _directions_of(slots: dict[datetime, dict[tuple[str, str], object]]) -> set[str]:
    return {direction for series in slots.values() for direction, _ in series}


def _check_slot(series: dict[tuple[str, str], object] | None, directions: set[str]) -> Check:
    """One quarter hour of one point: complete (every series of `directions`) and total = LEG + grid."""
    check = Check(expected=1)
    if not series:
        return check
    complete = all((direction, energy_type) in series for direction in directions for energy_type in ENERGY_TYPES)
    check.complete = int(complete and bool(directions))
    worst = Decimal(0)
    for direction in directions:
        parts = [series.get((direction, energy_type)) for energy_type in ENERGY_TYPES]
        if all(part is not None for part in parts):
            total, local, residual = (part.value for part in parts)
            worst = max(worst, abs(total - local - residual))
    if worst > SDAT_RESOLUTION_KWH:
        check.mismatched = 1
    elif worst:
        check.rounded = 1
    return check


def _add_slot(flows: Flows, series: dict[tuple[str, str], object] | None, directions) -> None:
    for (direction, energy_type), official in (series or {}).items():
        if direction in directions:
            flows.add(direction, energy_type, official.value, official.condition)


def _period_rows(start: date, end: date) -> tuple[list[PeriodRow], str]:
    rows: list[PeriodRow] = []
    if (end - start).days >= _MONTH_ROWS_FROM_DAYS:
        month = start.replace(day=1)
        while month < end:
            rows.append(PeriodRow(start=max(month, start), end=min(_next_month(month), end)))
            month = _next_month(month)
        return rows, "month"
    day = start
    while day < end:
        rows.append(PeriodRow(start=day, end=day + timedelta(days=1)))
        day += timedelta(days=1)
    return rows, "day"


def _rows_by_day(rows: list[PeriodRow]) -> dict[date, PeriodRow]:
    by_day = {}
    for row in rows:
        day = row.start
        while day < row.end:
            by_day[day] = row
            day += timedelta(days=1)
    return by_day


def _check_period(span: tuple[date, date]) -> None:
    start, end = span
    if end <= start:
        raise ValueError("Das Ende des Zeitraums muss nach dem Anfang liegen.")


# ── Public functions ────────────────────────────────────────────────────────


def meter_choices(db_path: Path) -> list[MeterChoice]:
    """Every metering point of the master data, active first, then by participant and label."""
    choices = list(_meter_index(db_path).values())
    return sorted(choices, key=lambda c: (not c.active, c.participant.lower(), c.meter_label.lower(),
                                          c.metering_point_id))


def data_until(db_path: Path) -> date | None:
    """Last local day the grid operator's E66 deliveries cover (any metering point), None without data."""
    end = official_data_end(db_path)
    return (end - _SLOT).astimezone(_TZ).date() if end else None


def period_sums(db_path: Path, start: date, end: date, *, metering_point_id: str | None = None) -> PeriodSums:
    """Sums per metering point in the local days [start, end), for one point or all.

    Completeness counts the quarter hours up to the latest delivery: a day the
    grid operator has not sent yet is not "missing".
    """
    _check_period((start, end))
    if (end - start).days > MAX_SUM_DAYS:
        raise ValueError(f"Höchstens {MAX_SUM_DAYS} Tage auf einmal.")
    rows, unit = _period_rows(start, end)
    result = PeriodSums(start=start, end=end, rows=rows, row_unit=unit, data_until=data_until(db_path))
    begin, finish = _local_midnight(start), _local_midnight(end)
    points = [metering_point_id] if metering_point_id else None
    data = load_official_values(db_path, begin, finish, document_type="E66", metering_point_ids=points)
    result.files = len(data.sources)
    grouped = _by_point_and_slot(data.values)
    meters = _meter_index(db_path)
    in_leg = _in_leg_rules(db_path)
    by_day = _rows_by_day(rows)
    data_end = official_data_end(db_path) or begin
    checked_until = min(finish, data_end)
    # Members without any delivery in the period are shown too: that is exactly what must stand out.
    # A meter of someone who joins later (or has left) is not expected and not listed.
    if metering_point_id:
        grouped.setdefault(metering_point_id.strip().upper(), {})
    else:
        for point, meter in meters.items():
            if (meter.active and meter.role != METER_ROLE_GRID and point not in grouped
                    and any(in_leg(point, day) for day in by_day)):
                grouped[point] = {}
    for point, slots in grouped.items():
        meter = meters.get(point)
        sums = PointSums(metering_point_id=meter.metering_point_id if meter else point,
                         meter_label=meter.meter_label if meter else "",
                         participant=meter.participant if meter else "", known=meter is not None)
        directions = _directions_of(slots)
        for slot, series in slots.items():
            _add_slot(sums.flows, series, DIRECTIONS)
            row = by_day.get(slot.astimezone(_TZ).date())
            if row is not None:
                _add_slot(row.flows, series, DIRECTIONS)
        incomplete_rows: set[date] = set()
        counted_rows: set[date] = set()
        for slot in _slots(begin, checked_until):
            series = slots.get(slot)
            day = slot.astimezone(_TZ).date()
            if not series and not in_leg(point, day):
                continue                    # not in the LEG that day: nothing expected
            check = _check_slot(series, directions)
            sums.check.merge(check)
            row = by_day.get(day)
            if row is not None:
                row.check.merge(check)
                counted_rows.add(row.start)
                if not check.complete:
                    incomplete_rows.add(row.start)
        for row in rows:
            row.points += row.start in counted_rows
            row.incomplete_points += row.start in incomplete_rows
        result.points.append(sums)
        result.total.merge(sums.flows)
        result.total_check.merge(sums.check)
    result.points.sort(key=lambda p: (not p.known, p.participant.lower(), p.meter_label.lower(), p.metering_point_id))
    return result


def point_values(db_path: Path, metering_point_id: str, start: date, end: date, *, resolution: str = "15min",
                 directions: tuple[str, ...] = DIRECTIONS, today: date | None = None) -> PointValues:
    """Values of one metering point in the local days [start, end): one row per quarter hour, hour or day.

    Every quarter hour up to today gets a row, also when nothing was delivered
    for it; rows after the latest delivery are marked "not delivered yet".
    """
    _check_period((start, end))
    if resolution not in RESOLUTIONS:
        raise ValueError("Unbekannte Auflösung.")
    if (end - start).days > MAX_DAYS[resolution]:
        raise ValueError(f"Höchstens {MAX_DAYS[resolution]} Tage bei dieser Auflösung.")
    directions = tuple(d for d in DIRECTIONS if d in directions) or DIRECTIONS
    result = PointValues(metering_point_id=metering_point_id, start=start, end=end, resolution=resolution,
                         directions=directions, data_until=data_until(db_path))
    begin = _local_midnight(start)
    finish = min(_local_midnight(end), _local_midnight((today or datetime.now(_TZ).date()) + timedelta(days=1)))
    data = load_official_values(db_path, begin, _local_midnight(end), document_type="E66",
                                metering_point_ids=[metering_point_id])
    result.files = len(data.sources)
    slots = _by_point_and_slot(data.values).get(metering_point_id.strip().upper(), {})
    present = _directions_of(slots) & set(directions) or set(directions)
    data_end = official_data_end(db_path) or begin
    in_leg = _in_leg_rules(db_path)
    point = metering_point_id.strip().upper()

    rows: dict[datetime, ValueRow] = {}
    for slot in _slots(begin, finish):
        local = slot.astimezone(_TZ)
        if resolution == "15min":
            key, row_end = slot, slot + _SLOT
        elif resolution == "hour":
            key = slot.replace(minute=0)
            row_end = key + timedelta(hours=1)
        else:
            key = _local_midnight(local.date())
            row_end = _local_midnight(local.date() + timedelta(days=1))
        row = rows.get(key)
        if row is None:
            row = rows[key] = ValueRow(start=key.astimezone(_TZ), end=row_end.astimezone(_TZ), zone=_zone(key))
        series = slots.get(slot)
        _add_slot(row.flows, series, directions)
        if series and any(direction in directions for direction, _ in series):
            row.has_values = True
        if not series and not in_leg(point, local.date()):
            row.member = False
            continue
        if slot >= data_end and not series:
            row.delivered = False
            continue
        check = _check_slot(series, present)
        row.check.merge(check)
        result.total_check.merge(check)
    for row in rows.values():
        result.total.merge(row.flows)
    result.rows = list(rows.values())
    return result


def quarter_hour_origin(db_path: Path, metering_point_id: str, slot: datetime) -> QuarterHourOrigin:
    """Every delivery of one quarter hour of one point, oldest first, and which value counts."""
    slot = slot.astimezone(timezone.utc)
    official = load_official_values(db_path, slot, slot + _SLOT, document_type="E66",
                                    metering_point_ids=[metering_point_id])
    effective = {(direction, energy_type): (value.value, value.condition, value.sha256)
                 for (_, direction, energy_type, _), value in official.values.items()}
    rows: list[OriginRow] = []
    previous: dict[tuple[str, str], Decimal] = {}
    for delivery in deliveries_of_quarter_hour(db_path, metering_point_id, slot):
        created = datetime.fromisoformat(delivery.created_at).astimezone(_TZ) if delivery.created_at else None
        row = OriginRow(file_name=delivery.file_name, created_at=created, cancellation=delivery.cancellation,
                        values=delivery.values)
        for key, (value, _) in delivery.values.items():
            in_effect = effective.get(key)
            if in_effect is not None and in_effect[2] == delivery.sha256:
                row.in_use.add(key)
            if key in previous and previous[key] != value:
                row.changed.add(key)
            previous[key] = value
        rows.append(row)
    return QuarterHourOrigin(
        metering_point_id=metering_point_id, start=slot.astimezone(_TZ), end=(slot + _SLOT).astimezone(_TZ),
        effective={key: (value, condition) for key, (value, condition, _) in effective.items()},
        deliveries=rows,
    )
