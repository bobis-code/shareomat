# -*- coding: utf-8 -*-
"""Turn meter values of a test into an official SDAT delivery of the grid operator (P2b).

Until 0.6.33 most billing tests wrote a CSV file into the inbox. Since 0.6.34
the only meter data source is the grid operator's SDAT delivery (ADR-0008,
ADR-0012). This helper keeps the tests readable: they still list
(timestamp, meter, kWh, direction) rows; `deliver` builds from them what the
grid operator would send and imports it through the real importer.

* LEG energy per quarter hour as the grid operator allocates it (Art. 19g
  StromVV, the rule of the former shareomat.core.pipeline.leg_matcher):
  L = min(P, C), received = import * L / C, supplied = export * L / P.
  Only `members` take part (default: every meter in the rows).
* Each LEG share is rounded to 0.001 kWh (SDAT resolution, ROUND_HALF_UP);
  residual = total - LEG share, so total = LEG + residual exactly.
* One E66 per calendar month whose report period covers the whole month (the
  monthly delivery the billing requires), plus the matching E31 sums.

All IDs and values are synthetic (tests/sdat_fixtures.py).
"""
from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable, Iterable
from zoneinfo import ZoneInfo

from shareomat.core.collector.sdat_import import import_sdat_file
from shareomat.database.community import get_community, save_community
from tests.sdat_fixtures import COMMUNITY, RECEIVER_EIC, Document, Series, build, eic

ZURICH = ZoneInfo("Europe/Zurich")
GRID_AREA = eic("10YCH-TESTAREA0")
SLOT = timedelta(minutes=15)
_RESOLUTION = Decimal("0.001")
_DIRECTION = {"import": "consumption", "export": "production"}

Row = tuple  # (timestamp: str | datetime, meter_id: str, kwh: str | Decimal | float, direction: "import" | "export")
Members = Callable[[str, datetime], bool] | Iterable[str] | None


def mpid(n: int) -> str:
    """A synthetic 33-character Swiss metering point ID (CH9999…)."""
    return f"CH9999{n:027d}"


def use_official_data(db: Path) -> None:
    """The community receives the grid operator's data: its EIC as the receiver of the deliveries."""
    community = get_community(db)
    assert community is not None, "save_community() first"
    save_community(db, replace(community, eic_code=RECEIVER_EIC))


def _utc(value) -> datetime:
    moment = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    assert moment.tzinfo is not None, f"timestamp without time zone: {value}"
    return moment.astimezone(timezone.utc)


def _kwh(value) -> Decimal:
    """A measured total; SDAT carries at most 3 decimals, so a finer test value is a mistake in the test."""
    amount = value if isinstance(value, Decimal) else Decimal(str(value))
    assert amount == amount.quantize(_RESOLUTION), f"SDAT carries at most 3 decimals: {value}"
    return amount


def _local(value: Decimal) -> Decimal:
    return value.quantize(_RESOLUTION, rounding=ROUND_HALF_UP)


def _is_member(members: Members) -> Callable[[str, datetime], bool]:
    if members is None:
        return lambda meter, slot: True
    if callable(members):
        return members
    allowed = {m.strip().upper() for m in members}
    return lambda meter, slot: meter.strip().upper() in allowed


def split(rows: Iterable[Row], members: Members = None) -> dict[tuple[str, str, datetime], tuple[Decimal, Decimal]]:
    """(meter, direction, slot) -> (total, LEG share) as the grid operator allocates it."""
    member = _is_member(members)
    totals: dict[tuple[str, str, datetime], Decimal] = {}
    for timestamp, meter, kwh, direction in rows:
        key = (meter.strip().upper(), _DIRECTION[direction], _utc(timestamp))
        assert key not in totals, f"value twice: {key}"
        totals[key] = _kwh(kwh)
    imports: dict[datetime, Decimal] = defaultdict(Decimal)
    exports: dict[datetime, Decimal] = defaultdict(Decimal)
    for (meter, direction, slot), total in totals.items():
        if member(meter, slot):
            (imports if direction == "consumption" else exports)[slot] += total
    out = {}
    for (meter, direction, slot), total in totals.items():
        side = imports[slot] if direction == "consumption" else exports[slot]
        shared = min(imports[slot], exports[slot])
        local = _local(total * shared / side) if member(meter, slot) and side > 0 else Decimal(0)
        out[(meter, direction, slot)] = (total, local)
    return out


def _runs(values: dict[datetime, Decimal]) -> list[tuple[datetime, list[Decimal]]]:
    """Contiguous quarter-hour runs: a gap in the data stays a gap (never 0 kWh)."""
    runs: list[tuple[datetime, list[Decimal]]] = []
    for slot in sorted(values):
        if runs and runs[-1][0] + SLOT * len(runs[-1][1]) == slot:
            runs[-1][1].append(values[slot])
        else:
            runs.append((slot, [values[slot]]))
    return runs


def _month_bounds(slot: datetime) -> tuple[datetime, datetime]:
    local = slot.astimezone(ZURICH)
    first = date(local.year, local.month, 1)
    following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
    as_utc = lambda d: datetime(d.year, d.month, d.day, tzinfo=ZURICH).astimezone(timezone.utc)
    return as_utc(first), as_utc(following)


def _series(point: str, direction: str, energy_type: str, values: dict[datetime, Decimal], community: str,
            conditions: dict[datetime, str]) -> list[Series]:
    return [Series(point, direction, energy_type, [f"{v:.3f}" for v in run],
                   [conditions.get(start + SLOT * i, "") for i in range(len(run))] if conditions else None,
                   community, start=start)
            for start, run in _runs(values)]


def documents(rows: Iterable[Row], *, members: Members = None, name: str = "lieferung", e31: bool = True,
              conditions: dict[datetime, str] | None = None, community: str = COMMUNITY,
              created: datetime | None = None, missing: Iterable[tuple] = ()) -> list[tuple[str, bytes]]:
    """(file name, SDAT bytes): per calendar month the monthly E66 delivery and, with `e31`, its E31 sums.

    `conditions` maps a slot to an SDAT quality code ("56" substitute, "21"
    provisional) for every series of that quarter hour. `created` orders
    deliveries: a later one supersedes the values of an earlier one.
    `missing` lists (timestamp, meter, direction, energy_type) values the
    delivery lacks although the grid operator allocated with them (a gap).
    """
    allocation = split(list(rows), members)
    gaps = {(meter.strip().upper(), _DIRECTION[direction], energy_type, _utc(timestamp))
            for timestamp, meter, direction, energy_type in missing}
    by_month: dict[tuple[datetime, datetime], dict] = defaultdict(lambda: defaultdict(dict))
    for (meter, direction, slot), (total, local) in allocation.items():
        flows = by_month[_month_bounds(slot)]
        for energy_type, value in (("total", total), ("local", local), ("residual", total - local)):
            if (meter, direction, energy_type, slot) not in gaps:
                flows[(meter, direction, energy_type)][slot] = value
    slot_conditions = {_utc(k): v for k, v in (conditions or {}).items()}
    out = []
    for (month_start, month_end), flows in sorted(by_month.items()):
        stamp = month_start.astimezone(ZURICH).strftime("%Y-%m")
        series: list[Series] = []
        sums: dict[tuple[str, str], dict[datetime, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
        for (meter, direction, energy_type), values in sorted(flows.items()):
            series += _series(meter, direction, energy_type, values, community, slot_conditions)
            for slot, value in values.items():
                sums[(direction, energy_type)][slot] += value
        kinds = [("E66", series)]
        if e31:
            kinds.append(("E31", [s for (direction, energy_type), values in sorted(sums.items())
                                  for s in _series(GRID_AREA, direction, energy_type, values, community, {})]))
        for doc_type, doc_series in kinds:
            document = Document(document_type=doc_type, series=doc_series, report_start=month_start,
                                report_end=month_end, document_id=f"{name}-{stamp}-{doc_type}")
            if created is not None:
                document.created = created
            out.append((f"{name}-{stamp}-{doc_type.lower()}.xml", build(document, month_start)))
    return out


def deliver(db: Path, directory: Path, rows: Iterable[Row], **options) -> list:
    """Import `rows` through the real SDAT importer as the grid operator's delivery (see documents()).

    Returns the import outcomes; every one must be imported.
    """
    outcomes = []
    for file_name, data in documents(rows, **options):
        path = directory / file_name
        path.write_bytes(data)
        outcome = import_sdat_file(db, path, hashlib.sha256(data).hexdigest(), file_name=file_name, origin="swisseldex")
        assert outcome.imported, outcome
        outcomes.append(outcome)
    return outcomes
