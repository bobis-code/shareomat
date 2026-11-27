# -*- coding: utf-8 -*-
"""
File: tests/synthetic/schema18.py

Purpose:
    Build a synthetic Shareomat installation on schema 18 that contains every
    data state the schema-19 migration has to handle (step P0a of
    the rebuild concept docs/development/Datenbank_Struktur_0.6.30.md, chapter 16.1;
    kept local for now). The
    migration and architecture tests use it instead of productive data.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    * Everything is created through the database functions the web UI uses,
      so every state is one the real code produces. Raw SQL is used only for
      states today's code no longer creates (see _legacy_states) and for two
      states the UI cannot reach (an expired session, an open review note).
    * The history lies before the real today; contract publishing and the
      notice/invoice "today" are moved back with _clock().
    * All names, addresses, IDs and values are synthetic (AGENTS.md rule 9).
    * Run standalone: python -m tests.synthetic.schema18 <out-dir>
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import shareomat.database.contract_notices as notices_module
import shareomat.database.contract_versions as versions_module
import shareomat.database.invoicing as invoicing_module
import shareomat.database.participant_contract as assignments_module
from shareomat.config import PathConfig, RuntimeConfig
from shareomat.core.collector.sdat_import import import_sdat_file
from shareomat.database import auth, billing, invoicing, live_sources
from shareomat.database.community import save_community
from shareomat.database.contract_notices import create_email_attempt, create_letter, finish_email, resolve_unclear
from shareomat.database.contract_settings import save_contract_settings
from shareomat.database.contract_versions import (
    end_contract,
    get_contract_version,
    publish_version,
    save_draft_version,
    withdraw_version,
)
from shareomat.database.data_imports import record_import
from shareomat.database.elcom_tariffs import save_elcom_tariffs
from shareomat.database.email_templates import save_email_template
from shareomat.database.exchange_rates import save_exchange_rates
from shareomat.database.external_settings import save_external_data_settings
from shareomat.database.memberships import confirm_review
from shareomat.database.meters import assign_meter_to_participant, create_meter, set_meter_active
from shareomat.database.participant_contract import (
    assign_contract,
    correct_assignment,
    list_assignments_for_participant,
    mark_accepted,
    mark_consent,
    mark_declaration_issued,
    mark_notified,
    mark_vnb_confirmed,
    mark_vnb_notified,
    record_departure,
    remove_signature,
)
from shareomat.database.participants import create_participant, set_participant_active, update_participant
from shareomat.database.reference_prices import save_reference_price
from shareomat.database.settings import get_operation_settings, save_operation_settings, save_swisseldex_schedule
from shareomat.database.sqlite import connect, init_db
from shareomat.database.supplier_tariffs import create_supplier_tariff
from shareomat.external_data.elcom import ElcomTariffComponents
from shareomat.external_data.market_forecast import ExchangeRate
from shareomat.leg_const import METER_DATA_SOURCE_SDAT_LEG
from shareomat.models.community import Community
from shareomat.models.contract import ContractSettings, ContractVersion
from shareomat.models.external_data import DataImportRecord, ExternalDataSettings, ReferencePrice, SupplierTariff
from shareomat.models.invoicing import PaymentProfile
from shareomat.models.live_source import ADAPTER_EMSOMAT_LOCAL, ADAPTER_HA_MQTT
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.web.pages.invoices import render_document
from shareomat.web.state import get_state
from tests.sdat_fixtures import COMMUNITY, OTHER_COMMUNITY, RECEIVER_EIC, Document, Series, build, eic

_TZ = ZoneInfo("Europe/Zurich")
_QUARTER = timedelta(minutes=15)

GRID_AREA_EIC = eic("10YCH-TESTAREA0")
IBAN = "CH93 0076 2011 6238 5295 7"            # example IBAN from the SIX documentation
ADMIN_USER = "admin"
ADMIN_PASSWORD = "Synthetisch-Test-Passwort-1"
ENTSOE_TEST_TOKEN = "SYNTHETIC-ENTSOE-TOKEN-0000"

# Per quarter hour (kWh): a billable consumption channel draws CONSUMER_TOTAL and gets
# CONSUMER_LOCAL from the LEG; producers share that local energy and feed PRODUCER_RESIDUAL
# into the grid on top. Non-billable channels get no LEG energy.
CONSUMER_TOTAL = Decimal("0.300")
CONSUMER_LOCAL = Decimal("0.120")
PRODUCER_RESIDUAL = Decimal("0.050")


@dataclass(frozen=True)
class Person:
    """One synthetic participant: code, label, meters (meter id, role) and the state it stands for."""

    code: str
    label: str
    meters: tuple[tuple[str, str], ...]
    stands_for: str


def _mpid(number: int) -> str:
    """A 33-character metering point ID that is obviously synthetic."""
    return f"CH9999999999999999999999{number:09d}"


PEOPLE: tuple[Person, ...] = (
    Person("P01", "Test Solar 1", ((_mpid(1), "producer"),), "Produzent mit Anlagenleistung, Unterschrift auf v1"),
    Person("P02", "Test Bezug 2", ((_mpid(2), "consumer"),), "Unterschrift mit hochgeladenem Dokument; Mitteilung v2 per E-Mail"),
    Person("P03", "Test Bezug 3", ((_mpid(31), "consumer"), (_mpid(32), "consumer")),
           "zwei Messpunkte; Unterschrift auf Papier; Mitteilung v2 als Brief"),
    Person("P04", "Test Prosumer 4", ((_mpid(4), "producer_consumer"),), "tritt mit v2 per 01.05.2026 bei, unterschreibt v2 direkt, wirksam ab 01.06.2026"),
    Person("P05", "Test Bezug 5", ((_mpid(5), "consumer"),), "Unterschrift entfernt und neu erfasst"),
    Person("P06", "Test Austritt 6", ((_mpid(6), "consumer"),), "Austritt per 30.06.2026; Messpunkt gültig bis dahin"),
    Person("P07", "Test Wiedereintritt 7", ((_mpid(7), "consumer"),),
           "Austritt per 30.04.2026, Wiedereintritt 01.09.2026, neue Meldung beim Netzbetreiber nicht bestätigt"),
    Person("P08", "Test Speicher 8", ((_mpid(8), "storage"),),
           "Speicher; Beitritt 01.11.2026 (nur im Entwurf November), gemeldet, nicht bestätigt"),
    Person("P09", "Test später wirksam 9", ((_mpid(9), "consumer"),), "wirksamer Beginn 01.04.2026, drei Monate nach dem Beitritt"),
    Person("P10", "Test ohne Unterschrift 10", ((_mpid(10), "consumer"),), "Erklärung ausgestellt, nie unterschrieben"),
    Person("P11", "Test inaktiv 11", ((_mpid(11), "consumer"),), "Teilnehmer und Messpunkt inaktiv, ohne Vertrag"),
    Person("P12", "Test Zustimmung fehlt 12", ((_mpid(12), "consumer"),),
           "keine Zustimmung zu v3; Mitteilung v2 zurückgenommen und als Brief neu"),
    Person("P13", "Test Namenswechsel 13", ((_mpid(13), "consumer"),), "Name und Adresse nach der ersten Abrechnung geändert"),
    Person("P14", "Test unklar 14", ((_mpid(14), "consumer"),), "Mitteilung v2 per E-Mail mit unklarem Ergebnis, danach geklärt"),
    Person("P15", "Test falsch zugeordnet 15", (), "hatte Messpunkt 15 irrtümlich, vor jeder Freigabe umgehängt"),
    Person("P16", "Test Messpunkt umgehängt 16", ((_mpid(15), "consumer"),), "bekam Messpunkt 15 von P15"),
)

# Contract versions: (key, valid_from, valid_until, local, admin fee, published on, consent of all).
# Admin fees differ between v1 and v2, so the April-May run has two fee windows. Documents with a fee
# are possible since 0.6.31 (B28: the money balance accounts for the fee).
_V1 = ("v1", date(2026, 1, 1), None, Decimal("0.20"), Decimal("0.005"), date(2025, 9, 1), False)
_V2 = ("v2", date(2026, 5, 1), None, Decimal("0.22"), Decimal("0.01"), date(2025, 12, 20), False)
_V3 = ("v3", date(2026, 9, 1), date(2027, 8, 31), Decimal("0.22"), Decimal("0.01"), date(2026, 8, 1), True)
_V4 = ("v4", date(2027, 10, 1), None, Decimal("0.23"), Decimal("0.01"), date(2026, 11, 10), False)
_V5 = ("v5", date(2027, 12, 1), None, Decimal("0.24"), Decimal("0.01"), date(2026, 11, 12), False)

SDAT_MONTHS = ((2026, 3), (2026, 4), (2026, 5), (2026, 10))   # monthly deliveries: DST start, April+May, DST end
NOVEMBER_DAYS = (1, 2, 3, 4, 5)                         # daily provisional deliveries, no monthly yet


@dataclass
class BuildResult:
    """Where the installation is and what was created, for the tests that use it."""

    db_path: Path
    runtime: RuntimeConfig
    version_ids: dict[str, int] = field(default_factory=dict)
    run_ids: dict[str, int] = field(default_factory=dict)
    document_ids: list[int] = field(default_factory=list)


# ── Private helpers ──────────────────────────────────────────────────────────


@contextmanager
def _clock(today: date):
    """Move "today" back for the functions that check dates against it."""

    class _Fixed(date):
        @classmethod
        def today(cls):
            return today

    saved = (versions_module.date, assignments_module.date, notices_module.local_today, invoicing_module.local_today)
    versions_module.date = _Fixed
    assignments_module.date = _Fixed
    notices_module.local_today = lambda: today
    invoicing_module.local_today = lambda: today
    try:
        yield
    finally:
        (versions_module.date, assignments_module.date,
         notices_module.local_today, invoicing_module.local_today) = saved


def _runtime(root: Path) -> RuntimeConfig:
    paths = {}
    for name in ("inbox", "archive", "reports", "state"):
        paths[name] = root / name
        paths[name].mkdir(parents=True, exist_ok=True)
    return RuntimeConfig(paths=PathConfig(**paths))


def _draft(key, valid_from, valid_until, local, admin) -> ContractVersion:
    return ContractVersion(
        community_id="", version=0, status="draft",
        contract_text_snapshot=f"<p>Synthetischer Vertrag {key}</p>",
        local_rate_chf_kwh=local, admin_fee_chf_kwh=admin, feed_in_rate_chf_kwh=local - admin,
        grid_operator="Test-Netzbetreiber", representative_name="Test Vertretung",
        representative_address_line="Testweg 1", representative_postal_code="9999",
        representative_city="Testdorf", representative_email="vertretung@example.ch",
        valid_from=valid_from, valid_until=valid_until,
    )


def _publish(db: Path, spec, *, accept_gap: bool = False) -> int:
    key, valid_from, valid_until, local, admin, published_on, consent = spec
    with _clock(published_on):
        version = save_draft_version(db, _draft(key, valid_from, valid_until, local, admin))
        publish_version(db, version.id, consent_of_all=consent, accept_gap=accept_gap)
    return version.id


def _assignment(db: Path, code: str, version_id: int):
    """The newest assignment of `code` to `version_id`."""
    rows = [a for a in list_assignments_for_participant(db, code) if a.contract_version_id == version_id]
    return max(rows, key=lambda a: a.id)


def _join(db: Path, code: str, version_id: int, joined: date, *, signed: date | None, vnb_confirmed: bool = True,
          effective: date | None = None, **signature) -> int:
    """Join, declaration, signature and grid-operator notice/confirmation as the checklist records them."""
    assignment = assign_contract(db, code, version_id, joined_at=joined)
    mark_declaration_issued(db, assignment.id, joined - timedelta(days=20))
    if signed is not None:
        if not (signature.get("document_location") or signature.get("paperless_id")):
            signature.setdefault("paper_location", f"Ordner LEG, Register {code}")
        mark_accepted(db, assignment.id, signed, method=signature.pop("method", "paper"),
                      signed_by=signature.pop("signed_by", code), recorded_by="test", **signature)
        mark_vnb_notified(db, assignment.id, joined - timedelta(days=10))
        if vnb_confirmed:
            mark_vnb_confirmed(db, assignment.id, joined - timedelta(days=5), effective or joined)
    return assignment.id


def _local_day(slot: datetime) -> date:
    return slot.astimezone(_TZ).date()


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=_TZ)
    end = datetime(year + (month == 12), month % 12 + 1, 1, tzinfo=_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def _billable_channels(day: date) -> tuple[list[str], list[str]]:
    """Which consumption and production channels get LEG energy on `day` (mirrors the scenario in PEOPLE)."""
    consumption, production = [], [_mpid(1)]

    def member(code: str) -> bool:
        spans = {
            "P02": (date(2026, 1, 1), None), "P03": (date(2026, 1, 1), None), "P06": (date(2026, 1, 1), date(2026, 6, 30)),
            "P07": (date(2026, 1, 1), date(2026, 4, 30)), "P09": (date(2026, 4, 1), None),
            "P12": (date(2026, 1, 1), date(2026, 8, 31)), "P13": (date(2026, 1, 1), None),
            "P14": (date(2026, 1, 1), None), "P16": (date(2026, 1, 1), None),
        }
        if code == "P07" and day >= date(2026, 9, 1):
            return True
        if code == "P08":
            return day >= date(2026, 11, 1)
        if code == "P04":
            return day >= date(2026, 6, 1)
        start, end = spans[code]
        return start <= day and (end is None or day <= end)

    for person in PEOPLE:
        if person.code in ("P01", "P05", "P10", "P11", "P15") or not person.meters:
            continue
        if member(person.code):
            consumption.extend(mpid for mpid, _role in person.meters)
    if day >= date(2026, 6, 1):
        production.append(_mpid(4))
    return consumption, production


def _flows_for_slot(slot: datetime, delivered: set[str]) -> dict[tuple[str, str], tuple[Decimal, Decimal]]:
    """(mpid, direction) -> (total, local) for one quarter hour; residual = total - local."""
    consumption, production = _billable_channels(_local_day(slot))
    consumption = [m for m in consumption if m in delivered]
    flows: dict[tuple[str, str], tuple[Decimal, Decimal]] = {}
    for mpid in delivered:
        role = next(r for p in PEOPLE for m, r in p.meters if m == mpid)
        if role in ("consumer", "producer_consumer", "storage"):
            local = CONSUMER_LOCAL if mpid in consumption else Decimal("0.000")
            flows[(mpid, "consumption")] = (CONSUMER_TOTAL, local)
        if role in ("producer", "producer_consumer", "storage"):
            flows[(mpid, "production")] = (Decimal("0.000"), Decimal("0.000"))
    pool = CONSUMER_LOCAL * len(consumption)
    producers = [m for m in production if m in delivered]
    for mpid in producers:
        share = (pool / len(producers)).quantize(Decimal("0.001"))
        flows[(mpid, "production")] = (share + PRODUCER_RESIDUAL, share)
    return flows


def _series_for(start: datetime, end: datetime, delivered: set[str], *, condition: str = "",
                community: str = COMMUNITY) -> list[Series]:
    slots = []
    slot = start
    while slot < end:
        slots.append(_flows_for_slot(slot, delivered))
        slot += _QUARTER
    keys = sorted(slots[0])
    out = []
    for mpid, direction in keys:
        totals = [f"{s[(mpid, direction)][0]:.3f}" for s in slots]
        locals_ = [f"{s[(mpid, direction)][1]:.3f}" for s in slots]
        residuals = [f"{s[(mpid, direction)][0] - s[(mpid, direction)][1]:.3f}" for s in slots]
        conditions = [condition] * len(slots) if condition else None
        for energy_type, values in (("total", totals), ("local", locals_), ("residual", residuals)):
            out.append(Series(mpid, direction, energy_type, values, conditions, community, start=start))
    return out


def _aggregate(series: list[Series], start: datetime) -> list[Series]:
    """E31 of the grid area: per direction and energy type the sum of all E66 series, per quarter hour."""
    sums: dict[tuple[str, str], list[Decimal]] = {}
    for s in series:
        values = [Decimal(v) for v in s.values]
        key = (s.direction, s.energy_type)
        sums[key] = [a + b for a, b in zip(sums[key], values)] if key in sums else values
    return [Series(GRID_AREA_EIC, direction, energy_type, [f"{v:.3f}" for v in values], start=start)
            for (direction, energy_type), values in sorted(sums.items())]


def _delivered_in(year: int, month: int) -> set[str]:
    """Metering points the grid operator reports for that month (valid, active, in the LEG)."""
    delivered = {m for p in PEOPLE for m, _r in p.meters if p.code not in ("P11",)}
    if (year, month) >= (2026, 7):
        delivered.discard(_mpid(6))          # P06's meter is valid until 30.06.2026
    return delivered


def _import(db: Path, root: Path, name: str, data: bytes) -> None:
    path = root / "sdat" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    import_sdat_file(db, path, hashlib.sha256(data).hexdigest(), file_name=name, origin="swisseldex")


def _setup_wizard_tariff(db: Path) -> None:
    """The tariff of the setup wizard (B3): created before any contract, so it has the lowest id and
    loses the tie on equal valid_from against the contract tariffs, as in real databases."""
    now = "2025-08-15T00:00:00+00:00"
    with connect(db) as conn:
        with conn:
            community = conn.execute("SELECT id FROM communities").fetchone()["id"]
            conn.execute(
                "INSERT INTO tariffs (community_id, name, local_rate_chf_kwh, grid_rate_chf_kwh, feed_in_rate_chf_kwh,"
                " valid_from, valid_until, active, created_at, updated_at) VALUES (?, 'Einrichtung', '0.18', '0',"
                " '0.17', '2026-01-01', '2026-12-31', 1, ?, ?)", (community, now, now))


def _people(db: Path) -> None:
    for person in PEOPLE:
        create_participant(db, Participant(person.code, person.label, email=f"{person.code.lower()}@example.ch",
                                           address_line=f"Testweg {person.code[1:]}", postal_code="9999",
                                           city="Testdorf", phone="+41 00 000 00 00"))
    for person in PEOPLE:
        for mpid, role in person.meters:
            owner = "P15" if mpid == _mpid(15) else person.code       # assigned wrongly first, moved below
            create_meter(db, Meter(mpid, owner, f"Zähler {person.code}", role,
                                   valid_until=date(2026, 6, 30) if person.code == "P06" else None,
                                   generation_kwp=Decimal("9.8") if role in ("producer", "producer_consumer") else None,
                                   connection_power="25 A"))
    assign_meter_to_participant(db, _mpid(15), "P16")                # P3: moved before any release
    set_meter_active(db, _mpid(11), False)
    set_participant_active(db, "P11", False)


def _contracts_and_members(db: Path, result: BuildResult) -> None:
    v1 = result.version_ids["v1"] = _publish(db, _V1)
    signed = date(2025, 12, 1)
    _join(db, "P01", v1, date(2026, 1, 1), signed=signed)
    _join(db, "P02", v1, date(2026, 1, 1), signed=signed, method="scan",
          document_location="documents/beitritte/P02-v1.pdf", document_sha256="0" * 64)
    _join(db, "P03", v1, date(2026, 1, 1), signed=signed, paper_location="Ordner LEG, Register 3")
    p05 = _join(db, "P05", v1, date(2026, 1, 1), signed=signed)
    remove_signature(db, p05, reason="falsches Datum erfasst")
    mark_accepted(db, p05, date(2025, 12, 3), method="paper", signed_by="P05", recorded_by="test",
                  paper_location="Ordner LEG, Register P05")
    for code in ("P06", "P07", "P12", "P13", "P14", "P16"):
        _join(db, code, v1, date(2026, 1, 1), signed=signed)
    _join(db, "P09", v1, date(2026, 1, 1), signed=signed, effective=date(2026, 4, 1))
    _join(db, "P10", v1, date(2026, 1, 1), signed=None)
    with _clock(date(2026, 4, 1)):
        record_departure(db, _assignment(db, "P07", v1).id, date(2026, 4, 30), reason="Umzug")

    v2 = result.version_ids["v2"] = _publish(db, _V2)
    _join(db, "P04", v2, date(2026, 5, 1), signed=date(2026, 4, 20), effective=date(2026, 6, 1))
    with _clock(date(2026, 6, 1)):
        record_departure(db, _assignment(db, "P06", v1).id, date(2026, 6, 30), reason="Verkauf der Liegenschaft")
    _notices_for_v2(db, v2)

    v3 = result.version_ids["v3"] = _publish(db, _V3)
    for code in ("P01", "P02", "P03", "P04", "P05", "P09", "P13", "P14", "P16"):
        mark_consent(db, _assignment(db, code, v3).id, date(2026, 8, 20), evidence="Zustimmung per E-Mail",
                     recorded_by="test")
    correct_assignment(db, _assignment(db, "P14", v3).id, "consent", {"consent_at": date(2026, 8, 21)},
                       reason="Datum der Zustimmung korrigiert", recorded_by="test")
    _join(db, "P07", v3, date(2026, 9, 1), signed=date(2026, 8, 25), vnb_confirmed=False)   # re-entry, signs v3
    _join(db, "P08", v3, date(2026, 11, 1), signed=date(2026, 10, 20), vnb_confirmed=False)

    with _clock(date(2026, 11, 9)):
        end_contract(db, v3, date(2027, 6, 30))                               # fixed term shortened (ended)
    result.version_ids["v4"] = _publish(db, _V4, accept_gap=True)              # gap 01.07.-30.09.2027 confirmed
    v5 = result.version_ids["v5"] = _publish(db, _V5)
    with _clock(date(2026, 11, 12)):
        mark_notified(db, _assignment(db, "P02", v5).id, date(2026, 11, 12), evidence="E-Mail vom 12.11.2026",
                      method="email", recorded_by="test")                     # evidence: archived, not deleted
    with _clock(date(2026, 11, 13)):
        withdraw_version(db, v5)                                              # v4's open end is restored
    with _clock(date(2026, 11, 14)):
        result.version_ids["v6"] = save_draft_version(db, _draft("v6", date(2029, 1, 1), None, Decimal("0.25"),
                                                                 Decimal("0.01"))).id


def _notices_for_v2(db: Path, v2: int) -> None:
    """Every way a contract notice can have been recorded (0.6.29/0.6.30 flow)."""
    version = get_contract_version(db, v2)
    text_sha = version.text_sha256

    def email(code: str, *, sent: bool, uncertain: bool = False):
        assignment = _assignment(db, code, v2)
        notice = create_email_attempt(
            db, assignment_id=assignment.id, participant_id=code, participant_label=code,
            contract_version_id=v2, contract_version=version.version, recipient=f"{code.lower()}@example.ch",
            subject="Mitteilung Vertragsänderung", body="Synthetischer Text", attachment_name="Vertrag.pdf",
            contract_sha256=text_sha, created_by="test",
        )
        finish_email(db, notice.id, sent=sent, uncertain=uncertain, detail="synthetisch", pdf_sha256="1" * 64,
                     recorded_by="test")
        return notice

    def letter(code: str, day: date):
        assignment = _assignment(db, code, v2)
        create_letter(db, assignment_id=assignment.id, participant_id=code, participant_label=code,
                      contract_version_id=v2, contract_version=version.version, method="letter", notice_date=day,
                      recipient=f"{code} Testweg, 9999 Testdorf", subject="Mitteilung Vertragsänderung",
                      body="Synthetischer Brief", attachment_name="Vertrag.pdf", contract_sha256=text_sha,
                      created_by="test")

    with _clock(date(2026, 1, 15)):
        email("P02", sent=True)
        letter("P03", date(2026, 1, 15))
        mark_notified(db, _assignment(db, "P05", v2).id, date(2026, 1, 16), evidence="persönlich übergeben",
                      method="handed", recorded_by="test")
        email("P06", sent=False)
        unclear = email("P14", sent=False, uncertain=True)
        email("P12", sent=True)
        email("P16", sent=False, uncertain=True)                               # stays unclear
    with _clock(date(2026, 1, 20)):
        resolve_unclear(db, unclear.id, found_in_sent=True, recorded_by="test")
        correct_assignment(db, _assignment(db, "P12", v2).id, "notice",
                           {"notified_at": None, "notice_method": "", "notice_evidence": ""},
                           recorded_by="test")                                # «Mitteilung zurücknehmen»
        letter("P12", date(2026, 1, 20))


def _sdat(db: Path, root: Path) -> None:
    for year, month in SDAT_MONTHS:
        start, end = _month_bounds(year, month)
        series = _series_for(start, end, _delivered_in(year, month))
        doc = Document(series=series, report_start=start, report_end=end, document_id=f"SYN-{year}{month:02d}",
                       created=end + timedelta(days=6))
        _import(db, root, f"e66-{year}-{month:02d}.xml", build(doc, start))
        totals = Document(document_type="E31", series=_aggregate(series, start), report_start=start, report_end=end,
                          document_id=f"SYN-E31-{year}{month:02d}", created=end + timedelta(days=6))
        _import(db, root, f"e31-{year}-{month:02d}.xml", build(totals, start))
    for day in NOVEMBER_DAYS:
        start = datetime(2026, 11, day, tzinfo=_TZ).astimezone(timezone.utc)
        end = start + timedelta(days=1)
        delivered = _delivered_in(2026, 11)
        provisional = Document(series=_series_for(start, end, delivered, condition="21"), report_start=start,
                               report_end=end, document_id=f"SYN-D{day:02d}", created=end + timedelta(hours=6))
        data = build(provisional, start)
        _import(db, root, f"e66-2026-11-{day:02d}.xml", data)
        if day == 1:
            _import(db, root, "e66-2026-11-01-copy.xml", data)                 # same file again: not imported twice
        if day == 4:
            bumped = list(provisional.series)
            residual = next(i for i, x in enumerate(bumped) if x.energy_type == "residual" and x.direction == "consumption")
            values = list(bumped[residual].values)
            values[0] = f"{Decimal(values[0]) + Decimal('0.001'):.3f}"           # total ≠ local + residual by 0.001
            bumped[residual] = replace(bumped[residual], values=values)
            rounded = replace(provisional, series=bumped, document_id="SYN-D04-RUNDUNG", created=end + timedelta(hours=8))
            _import(db, root, "e66-2026-11-04-rundung.xml", build(rounded, start))
        if day == 3:
            mixed = replace(provisional, document_id="SYN-D03-GEMISCHT", created=end + timedelta(hours=7),
                            series=provisional.series + _series_for(start, end, {_mpid(2)}, condition="21",
                                                                    community=OTHER_COMMUNITY)[:1])
            _import(db, root, "e66-2026-11-03-konflikt.xml", build(mixed, start))   # own meter, foreign LEG: refused
        if day == 5:
            foreign = Series(_mpid(999), "consumption", "total", ["5.000"] * len(provisional.series[0].values),
                             community=OTHER_COMMUNITY, start=start)              # meter of another LEG
            mixed = replace(provisional, document_id="SYN-D05-GEMISCHT", created=end + timedelta(hours=7),
                            series=provisional.series + [foreign])
            _import(db, root, "e66-2026-11-05-gemischt.xml", build(mixed, start))   # stored, foreign series counted
        if day == 2:
            corrected = replace(provisional, series=_series_for(start, end, delivered, condition="56"),
                                document_id="SYN-D02-K", created=end + timedelta(days=2))
            _import(db, root, "e66-2026-11-02-korrektur.xml", build(corrected, start))
            aggregate = Document(document_type="E31", report_start=start, report_end=end, document_id="SYN-E31-02",
                                 created=end + timedelta(days=1), series=[
                                     Series(GRID_AREA_EIC, "consumption", "total", ["1.000"] * 96, start=start),
                                 ])
            _import(db, root, "e31-2026-11-02.xml", build(aggregate, start))
    start = datetime(2026, 11, 1, tzinfo=_TZ).astimezone(timezone.utc)
    foreign = Document(series=_series_for(start, start + timedelta(days=1), {_mpid(2)}, community=OTHER_COMMUNITY),
                       document_id="SYN-FREMD", report_start=start, report_end=start + timedelta(days=1))
    _import(db, root, "e66-fremde-leg.xml", build(foreign, start))
    wrong_receiver = Document(series=_series_for(start, start + timedelta(days=1), {_mpid(2)}),
                              receiver_eic=eic("12X-TESTOTHER00"), document_id="SYN-FALSCH")
    _import(db, root, "e66-falscher-empfaenger.xml", build(wrong_receiver, start))


def _billing(db: Path, runtime: RuntimeConfig, result: BuildResult) -> None:
    def preview(first: tuple[int, int], last: tuple[int, int], *, persist: bool = False):
        # Only the November draft writes its readings into meter_readings (B5); the others compute
        # like the release check does, so the frozen fixture stays small.
        start = date(first[0], first[1], 1)
        end = date(last[0] + (last[1] == 12), last[1] % 12 + 1, 1) - timedelta(days=1)
        return billing.compute_billing_preview(db, runtime, start, end, persist_readings=persist)

    def clean(computed, key: str):
        if computed.availability.blocking_issues:
            raise RuntimeError(f"synthetic {key} is not clean: {computed.availability.blocking_issues[:3]}")
        return computed

    cancelled = billing.save_draft(db, preview((2026, 3), (2026, 3)))
    billing.cancel_billing_run(db, cancelled.id)
    result.run_ids["2026-03-cancelled"] = cancelled.id
    # Second version for the same period, released without any check point.
    run = billing.save_draft(db, clean(preview((2026, 3), (2026, 3)), "2026-03"))
    billing.release_billing_run(db, run.id, runtime=runtime)
    result.run_ids["2026-03"] = run.id
    # Two months with the price change of v2 on 01.05.2026 inside the period.
    run = billing.save_draft(db, clean(preview((2026, 4), (2026, 5)), "2026-04..05"))
    billing.release_billing_run(db, run.id, runtime=runtime)
    result.run_ids["2026-04..05"] = run.id
    october = preview((2026, 10), (2026, 10))
    if october.availability.hard_issues:
        raise RuntimeError(f"synthetic 2026-10 has hard issues: {october.availability.hard_issues[:3]}")
    run = billing.save_draft(db, october)
    billing.release_billing_run(db, run.id, override_reason="Bestätigung des Netzbetreibers für P07 folgt",
                                runtime=runtime)
    result.run_ids["2026-10-override"] = run.id
    result.run_ids["2026-11-draft"] = billing.save_draft(db, preview((2026, 11), (2026, 11), persist=True)).id


def _documents(db: Path, result: BuildResult) -> None:
    invoicing.save_payment_profile(db, PaymentProfile(payee="Test-LEG Konto", street="Testweg", building="1",
                                                      postal_code="9999", city="Testdorf", iban=IBAN,
                                                      contact_email="leg@example.ch"))
    invoicing.confirm_payment_profile(db, version=invoicing.get_payment_profile(db).version)
    march = invoicing.issue_documents(db, result.run_ids["2026-03"], render_document, today=date(2026, 4, 5))
    result.document_ids = [d.id for d in march]
    invoices = sorted((d for d in march if d.doc_type == "invoice"), key=lambda d: d.id)
    credits = [d for d in march if d.doc_type == "credit"]
    paid_full, paid_part, unpaid, refunded, replaced = invoices[:5]
    for doc in march:
        if doc.id != replaced.id:
            invoicing.record_delivery(db, doc.id, "paper" if doc.id == unpaid.id else "email")
    invoicing.record_payment(db, paid_full.id, paid_full.amount_chf, date(2026, 4, 20), reference="SYN-1",
                             today=date(2026, 4, 21))
    invoicing.record_payment(db, paid_part.id, (paid_part.amount_chf / 2).quantize(Decimal("0.01")),
                             date(2026, 4, 22), reference="SYN-2", today=date(2026, 4, 23))
    invoicing.record_payment(db, refunded.id, refunded.amount_chf + Decimal("5.00"), date(2026, 4, 24),
                             reference="SYN-3", today=date(2026, 4, 25))
    invoicing.record_refund(db, refunded.id, Decimal("5.00"), date(2026, 4, 30), reference="SYN-3R",
                            today=date(2026, 5, 1))
    if credits:
        invoicing.record_payout(db, credits[0].id, min(credits[0].amount_chf, paid_full.amount_chf),
                                date(2026, 5, 2), reference="SYN-P1", today=date(2026, 5, 3))
    reminder = invoicing.create_reminder(db, unpaid.id, render_document, today=date(2026, 6, 10))
    invoicing.record_delivery(db, unpaid.id, "paper", item="reminder", reminder_id=reminder.id)
    second = invoicing.create_reminder(db, paid_part.id, render_document, today=date(2026, 6, 10))
    invoicing.discard_reminder(db, second.id, "Restzahlung angekündigt")
    invoicing.set_reminder_block(db, paid_part.id, "Ratenzahlung vereinbart")
    invoicing.record_email_attempt(db, replaced.id, detail="synthetisch")
    invoicing.record_email_result(db, replaced.id, sent=True, detail="synthetisch")
    invoicing.cancel_document(db, replaced.id, "Adresse falsch", render_document, today=date(2026, 4, 12))
    invoicing.record_delivery(db, replaced.id, "email", item="cancellation")    # Stornobeleg zugestellt
    replacement = invoicing.issue_documents(db, result.run_ids["2026-03"], render_document, today=date(2026, 4, 13))
    result.document_ids.extend(d.id for d in replacement)
    two_windows = invoicing.issue_documents(db, result.run_ids["2026-04..05"], render_document, today=date(2026, 6, 5))
    result.document_ids.extend(d.id for d in two_windows)
    for doc in two_windows:
        invoicing.record_delivery(db, doc.id, "email")
    update_participant(db, "P13", Participant("P13", "Test Namenswechsel 13 (neu)", email="p13-neu@example.ch",
                                              address_line="Neuweg 13", postal_code="9998", city="Neudorf",
                                              phone="+41 00 000 00 13"))


def _settings(db: Path) -> None:
    save_operation_settings(db, replace(get_operation_settings(db), meter_data_source=METER_DATA_SOURCE_SDAT_LEG,
                                        auto_create_billing=True, auto_create_invoices=True, auto_send_invoices=False))
    save_contract_settings(db, ContractSettings(representative_name="Test Vertretung", representative_address_line="Testweg 1",
                                                representative_postal_code="9999", representative_city="Testdorf",
                                                representative_email="vertretung@example.ch",
                                                grid_operator="Test-Netzbetreiber", distribution_method="statisch"))
    save_email_template(db, "invoice", "Ihre Rechnung {belegnummer}",
                        "Guten Tag {name}\n\nSynthetische Vorlage für {belegnummer}.")
    save_swisseldex_schedule(db, True, 6)
    save_external_data_settings(db, ExternalDataSettings(entsoe_api_token=ENTSOE_TEST_TOKEN, municipality_bfs_number="9999"))


def _accounts(db: Path) -> None:
    admin = auth.create_admin(db, ADMIN_USER, ADMIN_PASSWORD, display_name="Test Admin")
    auth.create_session(db, admin.id, remember=False, user_agent="synthetic")
    _token, remembered = auth.create_session(db, admin.id, remember=True, user_agent="synthetic-remember")
    _token, revoked = auth.create_session(db, admin.id, remember=False, user_agent="synthetic-revoked")
    auth.revoke_session(db, revoked.id)
    _token, expired = auth.create_session(db, admin.id, remember=False, user_agent="synthetic-expired")
    with connect(db) as conn:
        with conn:
            conn.execute("UPDATE auth_sessions SET expires_at = ? WHERE id = ?", ("2026-01-01T00:00:00+00:00", expired.id))
    auth.authenticate_password(db, ADMIN_USER, "falsch", "192.0.2.10")
    auth.authenticate_password(db, ADMIN_USER, ADMIN_PASSWORD, "192.0.2.10")


def _live(db: Path) -> None:
    active = live_sources.create_live_source(db, meter_id=_mpid(4), adapter=ADAPTER_EMSOMAT_LOCAL,
                                             edge_node_id="synthetic-node", max_power_w=11000, freshness_evidence="A")
    live_sources.activate_live_source(db, active)
    live_sources.create_live_source(db, meter_id=_mpid(2), adapter=ADAPTER_HA_MQTT, device_id="synthetic-ha",
                                    measurement_mode="import_only", no_export_note="nur Bezug, kein Rücklieferzähler",
                                    adapter_config={"import_entity": "sensor.synthetic_p02_import"},
                                    max_power_w=8000, freshness_evidence="C")
    retired = live_sources.create_live_source(db, meter_id=_mpid(31), adapter=ADAPTER_HA_MQTT,
                                              device_id="synthetic-old",
                                              adapter_config={"import_entity": "sensor.synthetic_p03_import",
                                                              "export_entity": "sensor.synthetic_p03_export"},
                                              max_power_w=8000, freshness_evidence="C")
    live_sources.retire_live_source(db, retired)
    live_sources.set_meter_live_state(db, meter_id=_mpid(10), state="no_measurement")


def _external(db: Path) -> None:
    create_supplier_tariff(db, SupplierTariff(source="TEST-VNB", valid_from=date(2026, 1, 1), product="Test Grau 2026",
                                              energy_rate_ht_chf_kwh=Decimal("0.150"), energy_rate_nt_chf_kwh=Decimal("0.120"),
                                              feed_in_rate_chf_kwh=Decimal("0.080")))
    save_reference_price(db, ReferencePrice(technology="pv", period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
                                            price_chf_kwh=Decimal("0.0712"), source="BFE-TEST", is_official=True))
    save_elcom_tariffs(db, "9999", 2026, [ElcomTariffComponents(
        category="H4", energy_chf_kwh=Decimal("0.12"), grid_chf_kwh=Decimal("0.10"), aidfee_chf_kwh=Decimal("0.023"),
        community_fees_chf_kwh=Decimal("0.01"), total_chf_kwh=Decimal("0.253"), fixcosts_chf_year=Decimal("60"),
        operator_iri=None, operator_name="Test-Netzbetreiber")])
    save_exchange_rates(db, "EUR", [ExchangeRate(period="2026-10", rate_chf_per_eur=Decimal("0.9312"))])
    record_import(db, DataImportRecord(source="BFE", data_type="reference_prices_quarterly", status="ok",
                                       fetched_at=datetime(2026, 4, 2, tzinfo=timezone.utc)))


def _legacy_states(db: Path) -> None:
    """States today's code no longer creates but older databases contain (B5, B6, B7)."""
    now = "2026-01-01T00:00:00+00:00"
    with connect(db) as conn:
        with conn:
            community = conn.execute("SELECT id FROM communities").fetchone()["id"]
            conn.execute(
                "INSERT INTO meter_readings (meter_id, slot_start, direction, value_kwh, quality, source_file, updated_at)"
                " VALUES (?, '2026-02-01T00:00:00+00:00', 'import', 0.25, 'valid', 'alt.csv', ?)", (_mpid(2), now))
            conn.execute(
                "INSERT INTO settlement_cycle_records (run_id, period_start, period_end, participant_id, participant_label,"
                " meter_ids, source_fingerprint, local_received_kwh, grid_import_kwh, local_cost_chf, grid_cost_chf,"
                " total_cost_chf, created_at) VALUES ('legacy-run', '2026-02-01', '2026-02-02', 'P02', 'Test Bezug 2',"
                " ?, 'f' , 0.1, 0.2, '0.02', '0', '0.02', ?)", (_mpid(2), now))
            conn.execute(
                "INSERT INTO consumption_forecasts (scope, participant_id, slot_start, forecast_kwh, quality, method,"
                " computed_at) VALUES ('leg', '', '2026-02-01T00:00:00+00:00', 1.5, 'ok', 'weekday_profile', ?)", (now,))
            conn.execute("INSERT INTO day_ahead_prices (slot_start, price_chf_kwh, computed_at)"
                         " VALUES ('2026-02-01T00:00:00+00:00', '0.0815', ?)", (now,))


def _review_notes(db: Path) -> None:
    """One confirmed and one open review note (schema-18 migration findings), set after all billing."""
    with connect(db) as conn:
        with conn:
            rows = conn.execute("SELECT m.id FROM memberships m JOIN participants p ON p.id = m.participant_id"
                                " WHERE p.participant_id IN ('P13', 'P14') ORDER BY p.participant_id").fetchall()
            for row in rows:
                conn.execute("UPDATE memberships SET review_note = ? WHERE id = ?",
                             ("Beitritt in v1 und v2 verschieden erfasst (synthetisch)", row["id"]))
    confirm_review(db, rows[0]["id"])


# ── Public interface ─────────────────────────────────────────────────────────


def build_schema18(out: Path) -> BuildResult:
    """Build the synthetic schema-18 installation in `out` (must not exist yet); return what was created."""
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"{out} is not empty")
    out.mkdir(parents=True, exist_ok=True)
    db = out / "shareomat.db"
    init_db(db)
    runtime = _runtime(out)
    state = get_state()
    state.register_db(db)
    state.register_runtime(runtime)
    result = BuildResult(db_path=db, runtime=runtime)

    save_community(db, Community(community_id="SYNTH-LEG", name="Synthetische Test-LEG", address_line="Testweg 1",
                                 postal_code="9999", city="Testdorf", eic_code=RECEIVER_EIC,
                                 vnb_registered_at=date(2025, 9, 15), vnb_confirmed_start=date(2026, 1, 1)))
    _setup_wizard_tariff(db)
    _people(db)
    _settings(db)
    _contracts_and_members(db, result)
    _sdat(db, out)
    _billing(db, runtime, result)
    _documents(db, result)
    _accounts(db)
    _live(db)
    _external(db)
    _legacy_states(db)
    _review_notes(db)
    return result


def content_fingerprint(db_path: Path) -> str:
    """SHA-256 over every table's rows, without values that differ between two builds by design.

    Left out: creation/update times, session tokens, password hashes and the per-build
    random parts of authentication; every business value is included.
    """
    volatile_suffixes = ("_at", "_on_time")
    volatile = {"token_hash", "csrf_token", "password_hash", "password_changed_at", "auth_at", "last_seen_at",
                "expires_at", "fetched_at", "computed_at", "first_checked_at", "checked_at", "attempted_at",
                "cancel_record_sha256"}   # hashes the cancellation time
    digest = hashlib.sha256()
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in tables:
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            kept = [c for c in columns if c not in volatile and not c.endswith(volatile_suffixes)]
            digest.update(f"{table}:{','.join(kept)}\n".encode())
            for row in conn.execute(f"SELECT {', '.join(kept)} FROM {table} ORDER BY rowid"):
                digest.update(repr(row).encode())
    finally:
        conn.close()
    return digest.hexdigest()


if __name__ == "__main__":
    target = Path(sys.argv[1])
    built = build_schema18(target)
    print(f"built {built.db_path}: runs {built.run_ids}, documents {len(built.document_ids)}")
    print("fingerprint", content_fingerprint(built.db_path))
