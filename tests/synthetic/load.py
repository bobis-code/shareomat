# -*- coding: utf-8 -*-
"""
File: tests/synthetic/load.py

Purpose:
    Load installation for step P0a part 2 and the measurements behind E14/E15
    of the rebuild concept: one LEG with 1000 synthetic participants (about
    1100 metering points) and monthly SDAT deliveries, then timings for a
    monthly billing preview, saving, release, issuing all documents and a
    backup copy.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    * Master data, contract, memberships, billing and documents go through the
      same database functions the web UI uses.
    * SDAT values are stored with sdat_store.store_document, the function the
      importer uses; only reading the XML file is skipped (a month of 1100
      metering points would be several hundred MB of XML, and the parser is
      covered by the functional tests).
    * Not part of the regular test suite (minutes, gigabytes). Run:
      python -m tests.synthetic.load <empty-dir> [--participants 1000] [--months 3] [--measure]
    * All values synthetic.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.core.pipeline.raw.sdat_ch import SdatDocument, SdatHeader, SdatSeries
from shareomat.database import billing, invoicing
from shareomat.database.community import save_community
from shareomat.database.contract_versions import publish_version, save_draft_version
from shareomat.database.meters import create_meter
from shareomat.database.participant_contract import (
    assign_contract,
    mark_accepted,
    mark_vnb_confirmed,
    mark_vnb_notified,
)
from shareomat.database.participants import create_participant
from shareomat.database.sdat_store import store_document
from shareomat.database.sqlite import init_db
from shareomat.models.community import Community
from shareomat.models.invoicing import PaymentProfile
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.web.pages.invoices import render_document
from shareomat.web.state import get_state
from tests.sdat_fixtures import RECEIVER_EIC, SENDER_EIC, eic
from tests.synthetic.schema18 import IBAN, _clock, _draft

_TZ = ZoneInfo("Europe/Zurich")
_QUARTER = timedelta(minutes=15)
COMMUNITY_ID = "SYNTH-LOAD"
SDAT_COMMUNITY = "100009-LOAD01"
GRID_AREA_EIC = eic("10YCH-LOADAREA0")
START = date(2024, 11, 1)
CONSUMER_TOTAL, CONSUMER_LOCAL, PRODUCER_RESIDUAL = Decimal("0.300"), Decimal("0.120"), Decimal("0.050")
PRODUCT = {"total": "8716867000030", "local": "2404050010123", "residual": "2404050010124"}


@dataclass
class Timing:
    step: str
    seconds: float
    detail: str = ""


@dataclass
class LoadResult:
    db_path: Path
    runtime: RuntimeConfig
    participants: int
    meters: int
    months: list[tuple[int, int]]
    timings: list[Timing] = field(default_factory=list)


# ── Private helpers ──────────────────────────────────────────────────────────


def _mpid(n: int) -> str:
    return f"CH8888888888888888888888{n:09d}"


def _layout(participants: int) -> list[tuple[str, list[tuple[str, str]]]]:
    """(participant code, [(mpid, role)]): 70 % consumers (every 10th with a second meter),
    20 % prosumers, 10 % producers."""
    people, meter_no = [], 0
    for i in range(1, participants + 1):
        code = f"L{i:04d}"
        kind = i % 10
        role = "producer" if kind == 0 else "producer_consumer" if kind in (1, 2) else "consumer"
        meters = []
        meter_no += 1
        meters.append((_mpid(meter_no), role))
        if role == "consumer" and i % 10 == 3:
            meter_no += 1
            meters.append((_mpid(meter_no), "consumer"))
        people.append((code, meters))
    return people


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=_TZ)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _shares(pool: Decimal, count: int) -> list[Decimal]:
    """Split `pool` into `count` parts of 0.001 kWh resolution that add up exactly."""
    unit = Decimal("0.001")
    base = (pool / count).quantize(unit, rounding="ROUND_DOWN")
    rest = int((pool - base * count) / unit)
    return [base + (unit if i < rest else 0) for i in range(count)]


def _sdat_month(year: int, month: int, people) -> tuple[SdatDocument, SdatDocument]:
    """The E66 monthly delivery of all metering points and its E31 sums (constant values per quarter hour)."""
    start, end = _month_bounds(year, month)
    slots = int((end - start) / _QUARTER)
    consumption = [m for _c, ms in people for m, r in ms if r in ("consumer", "producer_consumer")]
    production = [m for _c, ms in people for m, r in ms if r in ("producer", "producer_consumer")]
    shares = _shares(CONSUMER_LOCAL * len(consumption), len(production))

    def series(point: str, direction: str, energy_type: str, value: Decimal, block: int, area: str = "") -> SdatSeries:
        return SdatSeries(block_id=f"B{block}", metering_point_id=point, grid_area_eic=area, direction=direction,
                          product_id=PRODUCT[energy_type], energy_type=energy_type, community_id=SDAT_COMMUNITY,
                          community_type="CT01", interval_start=start, interval_end=end, values=(value,) * slots,
                          conditions=("",) * slots)

    e66, block = [], 0
    for mpid in consumption:
        for energy_type, value in (("total", CONSUMER_TOTAL), ("local", CONSUMER_LOCAL),
                                   ("residual", CONSUMER_TOTAL - CONSUMER_LOCAL)):
            block += 1
            e66.append(series(mpid, "consumption", energy_type, value, block))
    for mpid, share in zip(production, shares):
        for energy_type, value in (("total", share + PRODUCER_RESIDUAL), ("local", share), ("residual", PRODUCER_RESIDUAL)):
            block += 1
            e66.append(series(mpid, "production", energy_type, value, block))
    sums: dict[tuple[str, str], Decimal] = {}
    for s in e66:
        sums[(s.direction, s.energy_type)] = sums.get((s.direction, s.energy_type), Decimal(0)) + s.values[0]
    e31 = [series("", direction, energy_type, total, i, GRID_AREA_EIC)
           for i, ((direction, energy_type), total) in enumerate(sorted(sums.items()), start=1)]

    def header(doc_type: str) -> SdatHeader:
        return SdatHeader(root_element="ValidatedMeteredData_16" if doc_type == "E66" else "AggregatedMeteredData_13",
                          document_type=doc_type, document_id=f"LOAD-{doc_type}-{year}{month:02d}", status="9",
                          created_at=end + timedelta(days=6), sender_eic=SENDER_EIC,
                          sender_role="MDR" if doc_type == "E66" else "DEA", receiver_eic=RECEIVER_EIC,
                          receiver_role="CEM", business_reason="C40", report_start=start, report_end=end,
                          acknowledgement_requested=False)

    return SdatDocument(header("E66"), tuple(e66)), SdatDocument(header("E31"), tuple(e31))


def _timed(result: LoadResult, step: str, func, detail=lambda value: ""):
    begin = time.perf_counter()
    value = func()
    result.timings.append(Timing(step, time.perf_counter() - begin, detail(value)))
    return value


# ── Public interface ─────────────────────────────────────────────────────────


def build_load(out: Path, *, participants: int = 1000, months: int = 24) -> LoadResult:
    """Build the load installation in the empty directory `out`."""
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    db = out / "shareomat.db"
    init_db(db)
    paths = {}
    for name in ("inbox", "archive", "reports", "state"):
        paths[name] = out / name
        paths[name].mkdir()
    runtime = RuntimeConfig(paths=PathConfig(**paths))
    get_state().register_db(db)
    get_state().register_runtime(runtime)
    month_list = []
    year, month = START.year, START.month
    for _ in range(months):
        month_list.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    people = _layout(participants)
    result = LoadResult(db, runtime, participants, sum(len(ms) for _c, ms in people), month_list)

    save_community(db, Community(community_id=COMMUNITY_ID, name="Synthetische Last-LEG", address_line="Lastweg 1",
                                 postal_code="9999", city="Testdorf", eic_code=RECEIVER_EIC,
                                 vnb_confirmed_start=START))
    invoicing.save_payment_profile(db, PaymentProfile(payee="Last-LEG Konto", street="Lastweg", building="1",
                                                      postal_code="9999", city="Testdorf", iban=IBAN,
                                                      contact_email="last@example.ch"))
    invoicing.confirm_payment_profile(db, version=invoicing.get_payment_profile(db).version)

    def master_data():
        with _clock(date(2024, 6, 1)):
            version = save_draft_version(db, _draft("v1", START, None, Decimal("0.20"), Decimal("0.01")))
            publish_version(db, version.id)
        for code, meters in people:
            create_participant(db, Participant(code, f"Last {code}", email=f"{code.lower()}@example.ch",
                                               address_line=f"Lastweg {code[1:]}", postal_code="9999", city="Testdorf"))
            for mpid, role in meters:
                create_meter(db, Meter(mpid, code, f"Zähler {code}", role))
            assignment = assign_contract(db, code, version.id, joined_at=START)
            mark_accepted(db, assignment.id, START - timedelta(days=30), method="paper", signed_by=code,
                          recorded_by="last", paper_location=f"Ordner Last, {code}")
            mark_vnb_notified(db, assignment.id, START - timedelta(days=20))
            mark_vnb_confirmed(db, assignment.id, START - timedelta(days=10), START)

    _timed(result, "Stammdaten, Vertrag, Mitgliedschaften", master_data,
           lambda _v: f"{participants} Teilnehmer, {result.meters} Messpunkte")

    def sdat():
        for year, month in month_list:
            for document in _sdat_month(year, month, people):
                sha = hashlib.sha256(f"{document.header.document_id}".encode()).hexdigest()
                store_document(db, sha256=sha, file_name=f"{document.header.document_id}.xml", origin="swisseldex",
                               document=document, community_id=SDAT_COMMUNITY)

    _timed(result, "SDAT speichern", sdat, lambda _v: f"{months} Monatslieferungen E66 + E31")
    return result


def measure(result: LoadResult, *, year: int, month: int) -> LoadResult:
    """Timings of one monthly billing for all participants (preview, save, release, documents, backup)."""
    db, runtime = result.db_path, result.runtime
    first = date(year, month, 1)
    last = date(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1)
    preview = _timed(result, "Monatsvorschau", lambda: billing.compute_billing_preview(db, runtime, first, last),
                     lambda p: f"{len(p.line_items)} Zeilen, Prüfpunkte: {len(p.availability.blocking_issues)}")
    if preview.availability.blocking_issues:
        raise RuntimeError(f"load billing not clean: {preview.availability.blocking_issues[:3]}")
    run = _timed(result, "Entwurf speichern", lambda: billing.save_draft(db, preview))
    _timed(result, "Freigabe (mit Nachrechnung)", lambda: billing.release_billing_run(db, run.id, runtime=runtime))
    _timed(result, "Alle Belege erstellen",
                  lambda: invoicing.issue_documents(db, run.id, render_document, today=last + timedelta(days=10)),
                  lambda d: f"{len(d)} Belege")

    def backup():
        target = db.with_name("backup-copy.db")
        source, copy = sqlite3.connect(db), sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
            source.close()
        size = target.stat().st_size
        target.unlink()
        return size

    _timed(result, "Online-Sicherung (Backup-API)", backup, lambda size: f"{size / 1e6:.0f} MB")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--participants", type=int, default=1000)
    parser.add_argument("--months", type=int, default=24)
    parser.add_argument("--measure", action="store_true")
    args = parser.parse_args()
    built = build_load(args.out, participants=args.participants, months=args.months)
    if args.measure:
        y, m = built.months[-1]
        measure(built, year=y, month=m)
    size = built.db_path.stat().st_size
    print(f"Datenbank: {size / 1e6:.0f} MB, {built.participants} Teilnehmer, {built.meters} Messpunkte, "
          f"{len(built.months)} Monate")
    for t in built.timings:
        print(f"  {t.step}: {t.seconds:.1f} s  {t.detail}")
