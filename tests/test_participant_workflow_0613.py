# -*- coding: utf-8 -*-
"""0.6.13: memberships, no fixed order, corrections, consent vs notice, collective actions, checklist.

Test plan B of docs/development/Teilnehmerverwaltung_Gesamtkonzept_0.6.13.md.
"""
from __future__ import annotations

import gzip
import itertools
import shutil
import sqlite3
from datetime import date, timedelta
from decimal import Decimal

import pytest

import shareomat.database.contract_versions as contract_versions_module
import shareomat.database.membership_migration as migration
from shareomat.config import PathConfig, RuntimeConfig, _parse_paperless
from shareomat.core.paperless import PaperlessValueError, document_link, normalize_base_url, parse_number
from shareomat.database.audit import list_events
from shareomat.database.billing import get_billing_run, released_runs_differing
from shareomat.database.billing_eligibility import EligibilityResolver
from shareomat.database.community import save_community
from shareomat.database.contract_versions import (
    ContractWorkflowError,
    get_contract_version,
    publish_version,
    save_draft_version,
    withdraw_version,
)
from shareomat.database.memberships import list_memberships
from shareomat.database.meters import create_meter
from shareomat.database.participant_checklist import DONE, OPEN, WAITING, build_checklist
from shareomat.database.participant_contract import (
    ParticipantContractError,
    apply_batch,
    assign_contract,
    correct_assignment,
    list_assignments_for_participant,
    list_assignments_for_version,
    mark_accepted,
    mark_consent,
    mark_declaration_issued,
    mark_notified,
    mark_vnb_confirmed,
    mark_vnb_notified,
    preview_batch,
    record_departure,
    remove_signature,
)
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.models.community import Community
from shareomat.models.contract import ContractVersion
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from tests.test_auth import Browser, _setup_admin, server  # noqa: F401  (fixture)

FIXTURES = __import__("pathlib").Path(__file__).parent / "fixtures" / "membership_migration"
METER = "CH1000000000000000000000000000T{:02d}"


def _fixed(value: date):
    class _Fixed(date):
        @classmethod
        def today(cls):
            return value
    return _Fixed


def _draft(valid_from: date, **overrides) -> ContractVersion:
    fields = dict(community_id="", version=0, status="draft", contract_text_snapshot="<p>Vertrag</p>",
                  local_rate_chf_kwh=Decimal("0.11"), feed_in_rate_chf_kwh=Decimal("0.105"),
                  admin_fee_chf_kwh=Decimal("0.005"), grid_operator="EBL", representative_name="Test Vertretung",
                  representative_address_line="Testweg 1", representative_postal_code="9999",
                  representative_city="Testdorf", representative_email="vertretung@example.ch",
                  valid_from=valid_from)
    fields.update(overrides)
    return ContractVersion(**fields)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "shareomat.db"
    init_db(path)
    save_community(path, Community(community_id="ZEV-TEST", name="Test-LEG", address_line="Testweg 1",
                                   postal_code="9999", city="Testdorf"))
    for n in range(1, 5):
        create_participant(path, Participant(f"p{n}", f"Haus {n}", email=f"p{n}@example.ch",
                                             address_line="Testweg 2", postal_code="9999", city="Testdorf"))
        create_meter(path, Meter(METER.format(n), f"p{n}", f"Zähler {n}", "producer" if n == 1 else "consumer"))
    monkeypatch.setattr(contract_versions_module, "date", _fixed(date(2025, 1, 1)))
    return path


def _v1(db, monkeypatch, valid_from=date(2026, 1, 1), **kw):
    monkeypatch.setattr(contract_versions_module, "date", _fixed(date(2025, 1, 1)))
    return publish_version(db, save_draft_version(db, _draft(valid_from, **kw)).id)


def _successor(db, monkeypatch, today, valid_from, **kw):
    monkeypatch.setattr(contract_versions_module, "date", _fixed(today))
    consent = kw.pop("consent_of_all", False)
    accept_gap = kw.pop("accept_gap", False)
    return publish_version(db, save_draft_version(db, _draft(valid_from, **kw)).id,
                           consent_of_all=consent, accept_gap=accept_gap)


def _sign(db, assignment_id, day=date(2025, 12, 1)):
    return mark_accepted(db, assignment_id, day, method="paper", signed_by="Test", paper_location="Ordner 1")


def _billed(db, day, participant="p2"):
    e = EligibilityResolver(db).for_day(day)
    meter = METER.format(int(participant[1:]))
    return meter in e.meters, e.excluded.get(meter, "")


# ── no fixed order ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("order", list(itertools.permutations(["issue", "sign", "report", "confirm"])))
def test_any_order_gives_the_same_result(db, monkeypatch, order):
    v1 = _v1(db, monkeypatch)
    a = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    steps = {
        "issue": lambda: mark_declaration_issued(db, a.id, date(2025, 11, 1)),
        "sign": lambda: _sign(db, a.id),
        "report": lambda: mark_vnb_notified(db, a.id, date(2025, 11, 2)),
        "confirm": lambda: mark_vnb_confirmed(db, a.id, date(2025, 12, 5), date(2026, 2, 1)),
    }
    for step in order:
        steps[step]()
    final = list_assignments_for_participant(db, "p2")[0]
    assert (final.declaration_issued_at, final.accepted_at, final.vnb_notified_at, final.vnb_confirmed_at,
            final.effective_start_at) == (date(2025, 11, 1), date(2025, 12, 1), date(2025, 11, 2),
                                          date(2025, 12, 5), date(2026, 2, 1))
    assert _billed(db, date(2026, 1, 15)) == (False, "vor dem vom Netzbetreiber bestätigten Beginn")
    assert _billed(db, date(2026, 2, 15)) == (True, "")


def test_report_before_signature_bills_only_once_signed(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    a = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    mark_vnb_notified(db, a.id, date(2025, 11, 1))
    assert _billed(db, date(2026, 1, 15)) == (False, "Beitritt nicht unterschrieben")
    _sign(db, a.id)
    assert _billed(db, date(2026, 1, 15)) == (True, "")


def test_a_second_meter_counts_from_its_own_start(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    a = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    _sign(db, a.id)
    create_meter(db, Meter(METER.format(22), "p2", "Zähler 2b", "consumer", valid_from=date(2026, 3, 1)))
    resolver = EligibilityResolver(db)
    assert METER.format(22) not in resolver.for_day(date(2026, 2, 15)).meters
    assert resolver.for_day(date(2026, 3, 15)).meters.get(METER.format(22)) == "p2"


# ── P1: one membership over all versions ───────────────────────────────────


def test_work_on_the_old_version_counts_in_the_announced_one(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    rows = {p: assign_contract(db, p, v1.id, v1.tariff_id, joined_at=date(2026, 1, 1)) for p in ("p2", "p3", "p4")}
    _sign(db, rows["p2"].id)
    _sign(db, rows["p4"].id)
    mark_vnb_notified(db, rows["p4"].id, date(2025, 11, 1))
    v2 = _successor(db, monkeypatch, date(2026, 9, 1), date(2027, 6, 1), representative_city="Neudorf")
    # recorded on the v1 rows after v2 was published:
    record_departure(db, rows["p2"].id, date(2027, 1, 31), reason="Wegzug")
    _sign(db, rows["p3"].id, date(2026, 9, 2))
    mark_vnb_confirmed(db, rows["p4"].id, date(2026, 9, 3), date(2026, 2, 1))
    v2rows = {a.participant_id: a for a in list_assignments_for_version(db, v2.id)}
    assert v2rows["p2"].left_at == date(2027, 1, 31)
    assert v2rows["p3"].accepted_at == date(2026, 9, 2)
    assert v2rows["p4"].effective_start_at == date(2026, 2, 1)
    day = date(2027, 7, 15)
    assert _billed(db, day, "p2") == (False, "nach dem Austritt")
    assert _billed(db, day, "p3") == (True, "")
    assert "p4" not in EligibilityResolver(db).for_day(day).unconfirmed_participants


def test_new_member_of_the_current_version_also_joins_the_announced_one(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    v2 = _successor(db, monkeypatch, date(2026, 9, 1), date(2027, 6, 1), representative_city="Neudorf")
    a = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 10, 1))
    [carried] = list_assignments_for_version(db, v2.id)
    assert carried.participant_id == "p2" and carried.membership_id == a.membership_id
    checklist = build_checklist(db, today=date(2026, 11, 16))
    assert any(t.kind == "notice" and t.version.id == v2.id for t in checklist.version_tasks)


def test_re_entry_is_a_new_membership_with_its_own_signature(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    first = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    _sign(db, first.id)
    record_departure(db, first.id, date(2026, 3, 31), reason="Umzug")
    with pytest.raises(ParticipantContractError, match="bereits"):
        assign_contract(db, "p3", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1)) and \
            assign_contract(db, "p3", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    second = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 7, 1))
    assert second.membership_id != first.membership_id and second.accepted_at is None
    assert len(list_memberships(db, "p2")) == 2
    assert _billed(db, date(2026, 2, 15)) == (True, "")                       # first membership
    assert _billed(db, date(2026, 5, 15)) == (False, "nach dem Austritt")     # between the two
    assert _billed(db, date(2026, 8, 15)) == (False, "Beitritt nicht unterschrieben")   # needs its own
    _sign(db, second.id, date(2026, 6, 20))
    assert _billed(db, date(2026, 8, 15)) == (True, "")


# ── notice is never consent ─────────────────────────────────────────────────


def test_consent_version_needs_consent_not_a_notice(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    a = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    _sign(db, a.id)
    v2 = _successor(db, monkeypatch, date(2026, 9, 1), date(2027, 1, 1), local_rate_chf_kwh=Decimal("0.12"),
                    feed_in_rate_chf_kwh=Decimal("0.115"), consent_of_all=True)
    [row] = list_assignments_for_version(db, v2.id)
    mark_notified(db, row.id, date(2026, 9, 5), method="email")
    assert _billed(db, date(2027, 1, 15)) == (False, "Zustimmung zu v2 fehlt")
    with pytest.raises(ParticipantContractError, match="Nachweis"):
        mark_consent(db, row.id, date(2026, 9, 6))
    mark_consent(db, row.id, date(2026, 9, 6), evidence="unterschriebene Änderung")
    assert _billed(db, date(2027, 1, 15)) == (True, "")
    new = assign_contract(db, "p3", v2.id, v2.tariff_id, joined_at=date(2027, 1, 1))
    _sign(db, new.id, date(2026, 9, 7))                    # signed v2 itself: no consent needed
    assert _billed(db, date(2027, 1, 15), "p3") == (True, "")


def test_contract_page_shows_consent_of_the_participant_not_only_the_grid_operator(db, monkeypatch):
    """Regression 18.11.2026: the v2 column showed the notice instead of the recorded consent."""
    from shareomat.web.pages import contract
    from shareomat.web.rendering import RequestContext
    from shareomat.web.state import get_state

    get_state().register_db(db)
    v1 = _v1(db, monkeypatch)
    a = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    _sign(db, a.id)
    mark_vnb_notified(db, a.id, date(2025, 11, 2))
    mark_vnb_confirmed(db, a.id, date(2025, 12, 5), date(2026, 1, 1))
    v2 = _successor(db, monkeypatch, date(2026, 9, 1), date(2027, 1, 1), local_rate_chf_kwh=Decimal("0.12"),
                    feed_in_rate_chf_kwh=Decimal("0.115"), consent_of_all=True)
    [row] = list_assignments_for_version(db, v2.id)

    def page():
        return contract.handle_get(RequestContext(method="GET", ingress_path="", segments=[], query={}))

    html = page()
    assert "Teilnahme (Netzbetreiber)" in html and "Vertrag v2" in html
    assert "vom Netzbetreiber bestätigt" in html
    assert "⏳ Zustimmung des Teilnehmers ausstehend" in html
    mark_consent(db, row.id, date(2026, 9, 6), evidence="unterschriebene Änderung")
    html = page()
    assert "✓ zugestimmt am 06.09.2026" in html
    assert "Zustimmung des Teilnehmers ausstehend" not in html


# ── P5: a fixed-term version is never extended ─────────────────────────────


def test_fixed_term_version_is_not_extended_and_gaps_need_confirmation(db, monkeypatch):
    v1 = _v1(db, monkeypatch, valid_until=date(2027, 2, 28))
    monkeypatch.setattr(contract_versions_module, "date", _fixed(date(2026, 11, 16)))
    draft = save_draft_version(db, _draft(date(2027, 4, 1), local_rate_chf_kwh=Decimal("0.12"),
                                          feed_in_rate_chf_kwh=Decimal("0.115")))
    with pytest.raises(ContractWorkflowError, match="01.03.2027 bis 31.03.2027 gilt kein Vertrag"):
        publish_version(db, draft.id)
    v2 = publish_version(db, draft.id, accept_gap=True)
    assert get_contract_version(db, v1.id).valid_until == date(2027, 2, 28)       # not extended
    withdraw_version(db, v2.id)
    assert get_contract_version(db, v1.id).valid_until == date(2027, 2, 28)       # and not opened either
    v3 = publish_version(db, save_draft_version(db, _draft(date(2027, 3, 1), local_rate_chf_kwh=Decimal("0.12"),
                                                           feed_in_rate_chf_kwh=Decimal("0.115"))).id,
                         consent_of_all=True)
    assert v3.valid_from == date(2027, 3, 1) and get_contract_version(db, v1.id).valid_until == date(2027, 2, 28)


def test_open_version_is_still_closed_by_its_successor(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    _successor(db, monkeypatch, date(2026, 9, 1), date(2027, 6, 1), representative_city="Neudorf")
    assert get_contract_version(db, v1.id).valid_until == date(2027, 5, 31)


# ── corrections ─────────────────────────────────────────────────────────────


@pytest.fixture
def released(tmp_path, monkeypatch):
    """The clean 0.6.12 reference after migration: has a released run for March 2026."""
    monkeypatch.setattr(migration, "_today", lambda: date(2026, 11, 16))
    path = tmp_path / "ref" / "shareomat.db"
    path.parent.mkdir()
    with gzip.open(FIXTURES / "v17_clean.db.gz", "rb") as src, path.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    init_db(path)
    for n in ("archive", "reports", "state"):
        (tmp_path / n).mkdir()
    runtime = RuntimeConfig(paths=PathConfig(inbox=FIXTURES / "inbox", archive=tmp_path / "archive",
                                             reports=tmp_path / "reports", state=tmp_path / "state"))
    return path, runtime


def _row(db, participant, version):
    return next(a for a in list_assignments_for_participant(db, participant) if a.version_number == version)


def test_administrative_correction_needs_no_reason_and_is_logged(released):
    db, _runtime = released
    a = _row(db, "c_left", 1)
    result = correct_assignment(db, a.id, "membership", {"exit_reason": "Verkauf"})
    assert result.affected_run_ids == [] and result.assignment.exit_reason == "Verkauf"
    event = list_events(db, entity="participant", entity_id="c_left")[0]
    assert event.action == "corrected" and "Austrittsgrund: Wegzug → Verkauf" in event.detail


def test_billing_correction_inside_a_released_period_needs_a_reason(released):
    db, runtime = released
    a = _row(db, "c_upload", 1)
    run_id = get_billing_run(db, 1).run.id
    before = get_billing_run(db, run_id).run.result_fingerprint
    with pytest.raises(ParticipantContractError, match="freigegebene Abrechnung"):
        correct_assignment(db, a.id, "membership", {"effective_start_at": date(2026, 4, 1)})
    result = correct_assignment(db, a.id, "membership", {"effective_start_at": date(2026, 4, 1)},
                                reason="EBL hat den Beginn auf April verschoben")
    assert result.affected_run_ids == [run_id]
    assert [r.id for r in released_runs_differing(db, runtime, result.affected_run_ids)] == [run_id]
    assert get_billing_run(db, run_id).run.result_fingerprint == before           # the run itself is untouched
    assert _row(db, "c_upload", 3).effective_start_at == date(2026, 4, 1)         # every version of the membership
    detail = list_events(db, entity="participant", entity_id="c_upload")[0].detail
    assert "wirksamer Beginn: 01.02.2026 → 01.04.2026" in detail and "Grund: EBL hat" in detail


def test_correction_validations(released):
    db, _runtime = released
    a = _row(db, "c_upload", 1)
    for values, message in (
        ({"joined_at": None}, "nicht entfernt"),
        ({"joined_at": date(2026, 1, 15)}, "Monatserster"),
        ({"left_at": date(2026, 6, 15)}, "Monatsletzter"),
        ({"effective_start_at": date(2025, 12, 1)}, "vor dem Beitritt"),
        ({"exit_reason": ""}, "Keine Änderung"),
    ):
        with pytest.raises(ParticipantContractError, match=message):
            correct_assignment(db, a.id, "membership", values, reason="Test")
    with pytest.raises(ParticipantContractError, match="Nachweis"):
        correct_assignment(db, _row(db, "c_pending", 1).id, "signature", {"paper_original_location": ""})


def test_signature_correction_and_removal(released):
    db, _runtime = released
    a = _row(db, "prod", 3)                                     # carried row: corrects the v1 signature
    result = correct_assignment(db, a.id, "signature", {"paper_original_location": "Ordner LEG, Register 9"})
    assert _row(db, "prod", 1).paper_original_location == "Ordner LEG, Register 9"
    assert result.assignment.paper_original_location == "Ordner LEG, Register 9"
    with pytest.raises(ParticipantContractError, match="freigegebene Abrechnung"):
        remove_signature(db, a.id)
    remove_signature(db, a.id, reason="irrtümlich erfasst")
    assert _row(db, "prod", 3).accepted_at is None
    assert "Unterschrift entfernt" in list_events(db, entity="participant", entity_id="prod")[0].detail


# ── collective actions ──────────────────────────────────────────────────────


def _data_version(db):
    conn = sqlite3.connect(db)
    try:
        return conn.execute("PRAGMA data_version").fetchone()[0], conn
    finally:
        pass


def test_collective_notice_preview_writes_nothing_and_apply_stores_per_participant(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    rows = {p: assign_contract(db, p, v1.id, v1.tariff_id, joined_at=date(2026, 1, 1)) for p in ("p2", "p3", "p4")}
    for r in rows.values():
        _sign(db, r.id)
    record_departure(db, rows["p4"].id, date(2027, 6, 30), reason="Wegzug")
    v2 = _successor(db, monkeypatch, date(2026, 9, 1), date(2027, 6, 1), representative_city="Neudorf")
    record_departure(db, rows["p3"].id, date(2027, 3, 31), reason="Kündigung")   # leaves before v2 starts
    ids = [a.id for a in list_assignments_for_version(db, v2.id)]
    conn = sqlite3.connect(db)
    before = conn.execute("PRAGMA data_version").fetchone()[0]
    check = preview_batch(db, "notice", ids, day=date(2027, 1, 10), method="email", evidence="Rundmail",
                          paperless_id=None, paperless_asn=None)
    assert conn.execute("PRAGMA data_version").fetchone()[0] == before            # preview: nothing written
    conn.close()
    assert sorted(p for _, p in check.ok) == ["p2", "p4"]
    assert check.skipped == [("p3", "vor Beginn dieser Version ausgetreten")]
    result = apply_batch(db, "notice", [a for a, _ in check.ok], recorded_by="admin", day=date(2027, 1, 10),
                         method="email", evidence="Rundmail", paperless_id=None, paperless_asn=None)
    assert sorted(result.done) == ["p2", "p4"] and result.batch_id
    stored = {a.participant_id: a for a in list_assignments_for_version(db, v2.id)}
    assert stored["p2"].notified_at == date(2027, 1, 10) and stored["p2"].notice_method == "email"
    assert stored["p2"].notice_batch_id == result.batch_id and stored["p2"].notice_recorded_by == "admin"
    again = apply_batch(db, "notice", ids, recorded_by="admin", day=date(2027, 1, 11), method="email",
                        evidence="", paperless_id=None, paperless_asn=None)
    assert again.done == [] and {p for p, _ in again.skipped} == {"p2", "p3", "p4"}   # never twice
    assert list_events(db, entity="batch", entity_id=result.batch_id)[0].action == "notice"


def test_collective_confirmation_checks_every_participant(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    early = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    late = assign_contract(db, "p3", v1.id, v1.tariff_id, joined_at=date(2026, 3, 1))
    result = apply_batch(db, "vnb_confirmed", [early.id, late.id], recorded_by="admin", day=date(2025, 12, 20),
                         effective_start=date(2026, 2, 1), evidence="Schreiben EBL", paperless_id=42,
                         paperless_asn=7)
    assert result.done == ["p2"] and "vor dem Beitritt" in result.skipped[0][1]
    stored = list_assignments_for_participant(db, "p2")[0]
    assert stored.vnb_confirmed_paperless_id == 42 and stored.effective_start_at == date(2026, 2, 1)


def test_an_unexpected_error_stores_nothing(db, monkeypatch):
    import shareomat.database.participant_contract as pc
    v1 = _v1(db, monkeypatch)
    rows = [assign_contract(db, p, v1.id, v1.tariff_id, joined_at=date(2026, 1, 1)).id for p in ("p2", "p3")]
    calls = []
    original = pc._update_membership

    def broken(conn, membership_id, **values):
        calls.append(membership_id)
        if len(calls) == 2:
            raise RuntimeError("Datenträger voll")
        return original(conn, membership_id, **values)
    monkeypatch.setattr(pc, "_update_membership", broken)
    with pytest.raises(RuntimeError):
        apply_batch(db, "vnb_notified", rows, recorded_by="admin", day=date(2025, 11, 1), paperless_id=None,
                    paperless_asn=None)
    assert all(a.vnb_notified_at is None for p in ("p2", "p3") for a in list_assignments_for_participant(db, p))


# ── Paperless ───────────────────────────────────────────────────────────────


def test_paperless_url_and_numbers_are_validated():
    assert normalize_base_url(" https://paperless.local:8000/ ") == "https://paperless.local:8000"
    assert normalize_base_url("") == ""
    for bad in ("javascript:alert(1)", "ftp://x", "https://user:pw@paperless.local", "http://p.local/?a=1",
                "http://p.local/#x", "http://", "http://p.local/a b", "//p.local"):
        with pytest.raises(PaperlessValueError):
            normalize_base_url(bad)
    assert parse_number(" 17 ", label="Nr.") == 17 and parse_number("", label="Nr.") is None
    for bad in ("0", "-1", "1.5", "abc", "1e3", "2147483648", "١٢"):
        with pytest.raises(PaperlessValueError):
            parse_number(bad, label="Nr.")
    assert document_link("https://p.local", 17) == "https://p.local/documents/17/details"
    assert document_link("", 17) == "" and document_link("https://p.local", None) == ""
    assert _parse_paperless({"paperless": {"url": "https://user:pw@evil.example"}}) == ""   # disabled, logged


def test_paperless_number_alone_is_enough_evidence(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    a = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    signed = mark_accepted(db, a.id, date(2025, 12, 1), method="scan", signed_by="Test", paperless_id=123,
                           paperless_asn=45)
    assert signed.signature_evidence == "paperless" and signed.signed_paperless_id == 123


# ── checklist ───────────────────────────────────────────────────────────────


def test_checklist_reads_only_and_follows_the_billing_rule(released):
    db, _runtime = released
    conn = sqlite3.connect(db)
    before = conn.execute("PRAGMA data_version").fetchone()[0]
    for month in (date(2026, 3, 1), date(2026, 11, 1), date(2027, 2, 1)):
        checklist = build_checklist(db, month=month, today=date(2026, 11, 16))
        resolver = EligibilityResolver(db)
        days = [month + timedelta(days=i) for i in range(28)]
        for row in checklist.rows:
            meters = [m.meter_id for m in row.meters]
            billable = [any(mid in resolver.for_day(d).meters for mid in meters) for d in days]
            state = row.points["billing"].state
            if all(billable):
                assert state in (DONE, WAITING), (row.participant.participant_id, month)
            elif not any(billable):
                assert state == OPEN, (row.participant.participant_id, month)
    assert conn.execute("PRAGMA data_version").fetchone()[0] == before
    conn.close()


def test_checklist_names_what_is_missing(db, monkeypatch):
    v1 = _v1(db, monkeypatch)
    a = assign_contract(db, "p2", v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    mark_vnb_notified(db, a.id, date(2025, 11, 1))
    rows = {r.participant.participant_id: r for r in build_checklist(db, today=date(2026, 11, 16)).rows}
    assert rows["p2"].points["contract"].state == OPEN and "Unterschrift" in rows["p2"].points["contract"].text
    assert rows["p2"].points["vnb"].state == WAITING
    assert rows["p3"].points["contract"].text == "keinem Vertrag zugeordnet"
    _sign(db, a.id)
    mark_vnb_confirmed(db, a.id, date(2025, 12, 1), date(2026, 1, 1))
    rows = {r.participant.participant_id: r for r in build_checklist(db, today=date(2026, 11, 16)).rows}
    assert rows["p2"].admin_complete and rows["p2"].points["billing"].state == DONE


def test_checklist_with_many_participants_stays_fast(db, monkeypatch):
    import time
    v1 = _v1(db, monkeypatch)
    for n in range(200):
        pid = f"m{n:03d}"
        create_participant(db, Participant(pid, f"Mitglied {n}", email=f"{pid}@example.ch"))
        create_meter(db, Meter(f"CH9{n:030d}", pid, "Zähler", "consumer"))
        assign_contract(db, pid, v1.id, v1.tariff_id, joined_at=date(2026, 1, 1))
    started = time.monotonic()
    checklist = build_checklist(db, today=date(2026, 11, 16))
    assert len(checklist.rows) == 204 and time.monotonic() - started < 20


# ── through the web server ──────────────────────────────────────────────────


def _prepare_server_data(srv, monkeypatch):
    db = srv["db"]
    save_community(db, Community(community_id="ZEV-TEST", name="Test-LEG"))
    for n in (2, 3):
        create_participant(db, Participant(f"p{n}", f"Haus {n}", email=f"p{n}@example.ch"))
        create_meter(db, Meter(METER.format(n), f"p{n}", f"Zähler {n}", "consumer"))
    v1 = _v1(db, monkeypatch)
    return {p: assign_contract(db, p, v1.id, v1.tariff_id, joined_at=date(2026, 1, 1)) for p in ("p2", "p3")}


def test_checklist_page_forms_come_back_to_the_participant(server, monkeypatch):  # noqa: F811
    rows = _prepare_server_data(server, monkeypatch)
    a = _setup_admin(server)
    status, _, html = a.get("/checklist")
    assert status == 200 and "LEG gesamt" in html and "Haus 2" in html
    status, _, html = a.get("/checklist?p=p2")
    assert "Fehlt noch" in html and "Unterschrift erfassen" in html
    csrf = a.page_csrf("/checklist?p=p2")
    status, headers, _ = a.post(f"/contract/participants/{rows['p2'].id}/vnb-notified",
                                {"csrf_token": csrf, "vnb_notified_at": "2025-11-01", "return_to": "checklist",
                                 "return_participant": "p2"})
    assert status == 303 and headers["Location"] == "/checklist?p=p2#detail"
    assert list_assignments_for_participant(server["db"], "p2")[0].vnb_notified_at == date(2025, 11, 1)
    status, headers, _ = a.post(f"/contract/participants/{rows['p3'].id}/vnb-notified",
                                {"csrf_token": csrf, "vnb_notified_at": "2025-11-01",
                                 "return_to": "https://evil.example/", "return_participant": "p3"})
    assert headers["Location"] == "/contract"                       # no open redirect


def test_collective_action_over_http_applies_once(server, monkeypatch):  # noqa: F811
    rows = _prepare_server_data(server, monkeypatch)
    a = _setup_admin(server)
    csrf = a.page_csrf("/checklist?batch=vnb_notified")
    status, headers, _ = a.post("/checklist/batch", {
        "csrf_token": csrf, "kind": "vnb_notified", "step": "preview", "batch_date": "2025-11-03",
        f"sel_{rows['p2'].id}": "on", f"sel_{rows['p3'].id}": "on"})
    assert status == 303 and "&t=" in headers["Location"]
    assert all(r.vnb_notified_at is None for r in list_assignments_for_version(server["db"], rows["p2"].contract_version_id))
    token = headers["Location"].split("&t=")[1]
    _, _, html = a.get(headers["Location"])
    assert "Vorschau — noch nichts gespeichert" in html and "Übernehmen (2)" in html
    form = {"csrf_token": a.page_csrf("/checklist"), "kind": "vnb_notified", "step": "apply", "token": token}
    a.post("/checklist/batch", form)
    assert all(r.vnb_notified_at == date(2025, 11, 3)
               for r in list_assignments_for_version(server["db"], rows["p2"].contract_version_id))
    form["csrf_token"] = a.page_csrf("/checklist")
    a.post("/checklist/batch", form)                                  # same token again
    _, _, html = a.get("/checklist")
    assert "abgelaufen oder wurde bereits übernommen" in html
    assert len([e for e in list_events(server["db"], entity="batch")]) == 1


def test_collective_consent_needs_a_common_document(server, monkeypatch):  # noqa: F811
    rows = _prepare_server_data(server, monkeypatch)
    a = _setup_admin(server)
    csrf = a.page_csrf("/checklist")
    a.post("/checklist/batch", {"csrf_token": csrf, "kind": "consent", "step": "preview", "batch_date": "2025-11-03",
                                f"sel_{rows['p2'].id}": "on"})
    _, _, html = a.get("/checklist")
    assert "gemeinsamer Nachweis nötig" in html


def test_detail_view_has_one_card_per_area_and_saving_comes_back(server, monkeypatch):  # noqa: F811
    _prepare_server_data(server, monkeypatch)
    a = _setup_admin(server)
    status, _, html = a.get("/checklist?p=p2")
    assert status == 200
    assert html.count('<section class="ck-section') == 7                           # one card per area
    for title in ("Stammdaten", "Messpunkte", "Vertrag &amp; Unterschrift", "EBL (Netzbetreiber)",
                  "Mitteilung / Zustimmung", "Abrechnung", "Austritt und Verlauf"):
        assert f">{title}" in html, title
    assert "consumer ·" not in html and "Verbraucher" in html                      # roles in plain German
    csrf = a.page_csrf("/checklist?p=p2")
    status, headers, _ = a.post("/participants/p2/edit", {
        "csrf_token": csrf, "participant_id": "p2", "label": "Haus 2", "address_line": "Neuer Weg 5",
        "postal_code": "9999", "city": "Testdorf", "email": "p2@example.ch", "active": "on",
        "return_to": "checklist", "return_participant": "p2"})
    assert status == 303 and headers["Location"] == "/checklist?p=p2#detail"
    _, _, html = a.get("/checklist?p=p2")
    assert 'value="Neuer Weg 5"' in html
    status, headers, _ = a.post("/meters/new", {
        "csrf_token": a.page_csrf("/checklist?p=p2"), "participant_id": "p2", "active": "on",
        "meter_id": METER.format(9), "label": "Zähler 2b", "role": "consumer", "valid_from": "", "valid_until": "",
        "return_to": "checklist", "return_participant": "p2"})
    assert headers["Location"] == "/checklist?p=p2#detail"
    _, _, html = a.get("/checklist?p=p2")
    assert METER.format(9) in html
