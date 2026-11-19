# -*- coding: utf-8 -*-
"""EBL join notice ("Dokumente → EBL-Meldungen") and the new fields phone, kWp, connection capacity."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

import shareomat.database.contract_versions as contract_versions_module
from shareomat.database.community import save_community
from shareomat.database.contract_settings import save_contract_settings
from shareomat.database.contract_versions import publish_version, save_draft_version
from shareomat.database.meters import create_meter, get_meter, update_meter
from shareomat.database.participant_contract import assign_contract
from shareomat.database.participant_documents import ebl_join_notice, ebl_join_notices
from shareomat.database.participants import create_participant, get_participant, update_participant
from shareomat.database.sqlite import init_db
from shareomat.models.community import Community
from shareomat.models.contract import ContractSettings
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.web.navigation import nav_group_for
from shareomat.web.pages import documents, meters, participants
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state
from tests.test_participant_workflow_0613 import _draft, _fixed

CONSUMER = "CH1000000000000000000000000000T01"
PRODUCER = "CH1000000000000000000000000000T02"


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    save_community(path, Community(community_id="ZEV-TEST", name="LEG Musterdorf", address_line="Testweg 1",
                                   postal_code="9999", city="Musterdorf"))
    save_contract_settings(path, ContractSettings(representative_name="Max Vertreter"))
    create_participant(path, Participant("p1", "Muster Erika", email="erika@example.ch", address_line="Testweg 2",
                                         postal_code="9999", city="Musterdorf", phone="061 000 00 00"))
    create_meter(path, Meter(CONSUMER, "p1", "Wohnung", "producer_consumer", generation_kwp=Decimal("9.8"),
                             connection_power="3 x 25 A"))
    monkeypatch.setattr(contract_versions_module, "date", _fixed(date(2026, 9, 1)))
    version = publish_version(path, save_draft_version(path, _draft(date(2026, 11, 1))).id)
    assign_contract(path, "p1", version.id, version.tariff_id, joined_at=date(2026, 12, 1))
    return path


def _get(*segments) -> RequestContext:
    return RequestContext(method="GET", ingress_path="", segments=list(segments), query={})


def _post(segments, form) -> RequestContext:
    return RequestContext(method="POST", ingress_path="", segments=list(segments),
                          form={**form, "csrf_token": get_state().csrf_token})


# ── new fields ──


def test_new_columns_are_added_to_an_existing_database(db):
    with sqlite3.connect(db) as conn:
        participant_columns = {r[1] for r in conn.execute("PRAGMA table_info(participants)")}
        meter_columns = {r[1] for r in conn.execute("PRAGMA table_info(meters)")}
    assert "phone" in participant_columns
    assert {"generation_kwp", "connection_power"} <= meter_columns
    init_db(db)                                                     # idempotent


def test_fields_are_stored_exactly(db):
    assert get_participant(db, "p1").phone == "061 000 00 00"
    meter = get_meter(db, CONSUMER)
    assert meter.generation_kwp == Decimal("9.8") and meter.connection_power == "3 x 25 A"
    update_meter(db, CONSUMER, Meter(CONSUMER, "p1", "Wohnung", "consumer"))
    meter = get_meter(db, CONSUMER)
    assert meter.generation_kwp is None and meter.connection_power == ""
    update_participant(db, "p1", Participant("p1", "Muster Erika"))
    assert get_participant(db, "p1").phone == ""


@pytest.mark.parametrize("phone", ["061<script>", "+41 61 " + "1" * 30, "a"])
def test_invalid_phone_is_refused(db, phone):
    with pytest.raises(FormError, match="Telefon"):
        participants.handle_post(_post(["p1", "edit"], {"participant_id": "p1", "label": "Muster Erika",
                                                        "phone": phone, "active": "on"}))


def _meter_form(**fields):
    form = {"meter_id": CONSUMER, "participant_id": "p1", "label": "Wohnung", "role": "producer", "active": "on"}
    form.update(fields)
    return form


def test_kwp_accepts_comma_and_refuses_nonsense(db):
    meters.handle_post(_post([CONSUMER, "edit"], _meter_form(generation_kwp="12,35", connection_power="  17   kVA ")))
    meter = get_meter(db, CONSUMER)
    assert meter.generation_kwp == Decimal("12.35") and meter.connection_power == "17 kVA"
    for bad in ("abc", "0", "-1", "100001", "1.2345", "NaN", "Infinity"):
        with pytest.raises(FormError, match="Erzeugungsanlage"):
            meters.handle_post(_post([CONSUMER, "edit"], _meter_form(generation_kwp=bad)))
    with pytest.raises(FormError, match="Anschlussleistung"):
        meters.handle_post(_post([CONSUMER, "edit"], _meter_form(connection_power="x" * 41)))
    assert get_meter(db, CONSUMER).generation_kwp == Decimal("12.35")       # refused input changes nothing


# ── notice ──


def test_complete_notice_has_every_field_of_the_ebl_form(db):
    notice = ebl_join_notice(db, "p1")
    assert notice.complete, notice.missing
    assert notice.community_name == "LEG Musterdorf" and notice.representative == "Max Vertreter"
    assert notice.object_address == "Testweg 2, 9999 Musterdorf"
    assert notice.meter_ids == [CONSUMER] and notice.generation_output == "9.8 kWp"
    assert notice.connection_power == "3 x 25 A" and notice.start == date(2026, 12, 1)


def test_missing_data_is_listed_not_invented(db):
    create_participant(db, Participant("p2", "Sonne Max"))
    create_meter(db, Meter(PRODUCER, "p2", "Haus", "producer"))
    notice = ebl_join_notice(db, "p2")
    assert not notice.complete
    for label in ("Objektadresse", "Leistung Erzeugungsanlage (kWp) beim Messpunkt",
                  "E-Mail", "Telefon", "Beitritt per (Mitgliedschaft)"):
        assert label in notice.missing
    # The connection capacity is optional: an orange hint, never a missing field.
    assert not any("Anschlussleistung" in m for m in notice.missing)
    assert notice.hints == ["Anschlussleistung nicht angegeben (freiwillig)"]
    assert ebl_join_notice(db, "p1").hints == []
    assert notice.generation_output == "" and notice.start is None
    assert [n.participant_id for n in ebl_join_notices(db)] == ["p1", "p2"]
    assert ebl_join_notice(db, "unknown") is None


def test_pages_render_escape_and_show_no_ebl_branding(db):
    update_participant(db, "p1", Participant("p1", "<b>Erika</b>", email="erika@example.ch", address_line="Testweg 2",
                                             postal_code="9999", city="Musterdorf", phone="061 000 00 00"))
    overview = documents.handle_get(_get())
    assert "&lt;b&gt;Erika&lt;/b&gt;" in overview and "<b>Erika</b>" not in overview
    assert "/documents/ebl/p1" in overview and "vollständig" in overview
    printed = documents.handle_get(_get("ebl", "p1"))
    assert "Bestätigung Eintritt in LEG durch neuen Teilnehmenden" in printed
    assert "Ich wurde über das LEG-Pflichtenheft und die Datenschutzrichtlinien" in printed
    assert "&lt;b&gt;Erika&lt;/b&gt;" in printed and "9.8 kWp" in printed and "01.12.2026" in printed
    assert "Mühlemattstrasse" not in printed and "ebl.ch" not in printed       # no EBL letterhead
    assert "nicht gefunden" in documents.handle_get(_get("ebl", "unknown"))


def test_menu_and_permission():
    from shareomat.security.permissions import required_permission
    assert nav_group_for("documents").label == "Dokumente"
    assert required_permission("GET", "documents", ["ebl", "p1"]) == "view"
