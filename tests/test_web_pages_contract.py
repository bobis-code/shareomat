# -*- coding: utf-8 -*-
"""Tests for the "Vertrag" admin page (shareomat.web.pages.contract)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import shareomat.database.contract_versions as contract_versions_module
from shareomat.database.community import save_community
from shareomat.database.contract_versions import get_contract_version, get_contract_version_for_date, list_contract_versions
from shareomat.database.participant_contract import list_assignments_for_version
from shareomat.database.participants import create_participant, get_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff
from shareomat.web.pages import contract
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    return path


def _fixed_today(value: date):
    class _Fixed(date):
        @classmethod
        def today(cls):
            return value
    return _Fixed


def _ctx(*, method="GET", segments=None, form=None, query=None) -> RequestContext:
    ctx = RequestContext(method=method, ingress_path="", form=form or {}, query=query or {})
    if segments is not None:
        ctx.segments = segments
    return ctx


def _post(segments, form=None) -> RequestContext:
    form = dict(form or {})
    form["csrf_token"] = get_state().csrf_token
    return _ctx(method="POST", segments=segments, form=form)


_PRICE_FORM = {
    "rate_mode": "flat",
    "vnb_reference_price_chf_kwh": "0.115",
    "price_reduction_chf_kwh": "0.01",
    "admin_fee_chf_kwh": "0.005",
    "grid_operator": "EBL",
    "price_notice_period_months": "4",
    "contract_notice_period_months": "6",
}


def test_renders_without_community(db_path) -> None:
    html = contract.handle_get(_ctx())
    assert "Vertrag" in html
    assert "Noch kein Vertrag in Kraft" in html


def test_renders_with_community(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    html = contract.handle_get(_ctx())
    assert "Test LEG" in html


def test_save_settings_persists_and_flashes(db_path) -> None:
    contract.handle_post(_post(["settings"], {"grid_operator": "EBL", "representative_name": "Jane Doe"}))
    settings = contract.get_contract_settings(db_path)
    assert settings.grid_operator == "EBL"
    assert settings.representative_name == "Jane Doe"


def test_save_settings_requires_grid_operator(db_path) -> None:
    with pytest.raises(FormError):
        contract.handle_post(_post(["settings"], {"grid_operator": ""}))


def test_draft_create_without_community_raises(db_path) -> None:
    with pytest.raises(FormError):
        contract.handle_post(_post(["draft", "create"], _PRICE_FORM))


def test_draft_create_creates_a_draft_version(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    contract.handle_post(_post(["draft", "create"], _PRICE_FORM))

    versions = list_contract_versions(db_path)
    assert len(versions) == 1
    assert versions[0].status == "draft"
    assert "Test LEG" in versions[0].contract_text_snapshot


def test_draft_create_derives_feed_in_and_local_rate_from_reference_price(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    contract.handle_post(_post(["draft", "create"], _PRICE_FORM))

    draft = list_contract_versions(db_path)[0]
    assert draft.feed_in_rate_chf_kwh == Decimal("0.105")  # 0.115 - 0.01
    assert draft.local_rate_chf_kwh == Decimal("0.11")     # 0.105 + 0.005


def test_draft_create_rejects_reduction_larger_than_reference_price(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    form = dict(_PRICE_FORM, vnb_reference_price_chf_kwh="0.05", price_reduction_chf_kwh="0.10")
    with pytest.raises(FormError):
        contract.handle_post(_post(["draft", "create"], form))


def test_draft_create_derives_ht_nt_rates_separately(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    form = dict(
        _PRICE_FORM, rate_mode="ht_nt",
        vnb_reference_price_nt_chf_kwh="0.09", price_reduction_nt_chf_kwh="0.02",
    )
    contract.handle_post(_post(["draft", "create"], form))

    draft = list_contract_versions(db_path)[0]
    assert draft.feed_in_rate_nt_chf_kwh == Decimal("0.07")   # 0.09 - 0.02
    assert draft.local_rate_nt_chf_kwh == Decimal("0.075")    # 0.07 + 0.005


def test_draft_update_edits_in_place(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    contract.handle_post(_post(["draft", "create"], _PRICE_FORM))
    draft = list_contract_versions(db_path)[0]

    updated_form = dict(
        _PRICE_FORM, vnb_reference_price_chf_kwh="0.20", price_reduction_chf_kwh="0", admin_fee_chf_kwh="0",
        version_id=str(draft.id),
    )
    contract.handle_post(_post(["draft", "update"], updated_form))

    versions = list_contract_versions(db_path)
    assert len(versions) == 1  # still the same row, not a new version
    assert versions[0].local_rate_chf_kwh == Decimal("0.20")


def test_draft_delete_removes_it(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    contract.handle_post(_post(["draft", "create"], _PRICE_FORM))
    draft = list_contract_versions(db_path)[0]

    contract.handle_post(_post(["draft", "delete"], {"version_id": str(draft.id)}))
    assert get_contract_version(db_path, draft.id) is None


def test_publish_makes_version_current(db_path, monkeypatch) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    contract.handle_post(_post(["draft", "create"], dict(_PRICE_FORM, valid_from="2026-01-01")))
    draft = list_contract_versions(db_path)[0]

    contract.handle_post(_post(["publish"], {"version_id": str(draft.id)}))
    monkeypatch.setattr(contract_versions_module, "date", date)

    html = contract.handle_get(_ctx())
    assert "v1" in html


def test_publish_too_early_raises_form_error(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    contract.handle_post(_post(["draft", "create"], dict(_PRICE_FORM, valid_from="2026-01-01")))
    draft = list_contract_versions(db_path)[0]

    with pytest.raises(FormError):
        contract.handle_post(_post(["publish"], {"version_id": str(draft.id)}))


def test_withdraw_reverts_scheduled_version_to_draft(db_path, monkeypatch) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 1, 1)))
    contract.handle_post(_post(["draft", "create"], dict(_PRICE_FORM, valid_from="2027-01-01")))
    draft = list_contract_versions(db_path)[0]
    contract.handle_post(_post(["publish"], {"version_id": str(draft.id)}))
    monkeypatch.setattr(contract_versions_module, "date", date)

    contract.handle_post(_post(["withdraw"], {"version_id": str(draft.id)}))
    assert get_contract_version(db_path, draft.id).status == "draft"


def test_end_requires_matching_community_name(db_path, monkeypatch) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2020, 1, 1)))
    contract.handle_post(_post(["draft", "create"], dict(_PRICE_FORM, valid_from="2020-06-01")))
    draft = list_contract_versions(db_path)[0]
    contract.handle_post(_post(["publish"], {"version_id": str(draft.id)}))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 6, 1)))

    with pytest.raises(FormError):
        contract.handle_post(_post(["end"], {
            "version_id": str(draft.id), "end_date": "2026-12-31", "confirm_name": "Wrong Name",
        }))

    contract.handle_post(_post(["end"], {
        "version_id": str(draft.id), "end_date": "2026-12-31", "confirm_name": "Test LEG",
    }))
    monkeypatch.setattr(contract_versions_module, "date", date)
    assert get_contract_version(db_path, draft.id).valid_until == date(2026, 12, 31)


def test_copy_from_prefills_form_for_new_draft(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    contract.handle_post(_post(["draft", "create"], dict(
        _PRICE_FORM, vnb_reference_price_chf_kwh="0.13", price_reduction_chf_kwh="0", admin_fee_chf_kwh="0",
    )))
    source = list_contract_versions(db_path)[0]

    html = contract.handle_get(_ctx(query={"copy_from": str(source.id)}))
    assert "0.13" in html
    assert "Neuen Entwurf erstellen" in html  # copying always creates a NEW draft, never edits the source


def test_edit_prefills_form_for_existing_draft(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    contract.handle_post(_post(["draft", "create"], dict(
        _PRICE_FORM, vnb_reference_price_chf_kwh="0.13", price_reduction_chf_kwh="0", admin_fee_chf_kwh="0",
    )))
    draft = list_contract_versions(db_path)[0]

    html = contract.handle_get(_ctx(query={"edit": str(draft.id)}))
    assert "Entwurf bearbeiten" in html
    assert "0.13" in html


def test_unknown_post_action_raises(db_path) -> None:
    with pytest.raises(FormError):
        contract.handle_post(_post(["nonsense"]))


# ── Teilnehmer / Beitrittserklärung ─────────────────────────────────────────


def _publish_v1(db_path, monkeypatch, *, valid_from: str = "2020-06-01") -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2020, 1, 1)))
    contract.handle_post(_post(["draft", "create"], dict(_PRICE_FORM, valid_from=valid_from)))
    draft = list_contract_versions(db_path)[0]
    contract.handle_post(_post(["publish"], {"version_id": str(draft.id)}))
    monkeypatch.setattr(contract_versions_module, "date", date)


def test_participants_add_requires_active_contract(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    with pytest.raises(FormError):
        contract.handle_post(_post(["participants", "add"], {"participant_id": "p1", "valid_from": "2027-01-01"}))


def test_participants_add_rejects_non_month_start(db_path, monkeypatch) -> None:
    _publish_v1(db_path, monkeypatch)
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    with pytest.raises(FormError):
        contract.handle_post(_post(["participants", "add"], {"participant_id": "p1", "valid_from": "2027-01-15"}))


def test_participants_add_creates_pending_assignment_and_sets_participant_valid_from(db_path, monkeypatch) -> None:
    _publish_v1(db_path, monkeypatch)
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    contract.handle_post(_post(["participants", "add"], {"participant_id": "p1", "valid_from": "2027-03-01"}))

    current_version = get_contract_version_for_date(db_path, date(2021, 1, 1))
    assignments = list_assignments_for_version(db_path, current_version.id)
    assert len(assignments) == 1
    assert assignments[0].accepted_at is None
    assert assignments[0].joined_at == date(2027, 3, 1)
    assert get_participant(db_path, "p1").valid_from == date(2027, 3, 1)


def test_participants_confirm_sets_accepted_and_notified(db_path, monkeypatch) -> None:
    _publish_v1(db_path, monkeypatch)
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    contract.handle_post(_post(["participants", "add"], {"participant_id": "p1", "valid_from": "2027-03-01"}))
    current_version = get_contract_version_for_date(db_path, date(2021, 1, 1))
    assignment = list_assignments_for_version(db_path, current_version.id)[0]

    contract.handle_post(_post(["participants", str(assignment.id), "confirm"], {"accepted_at": "2027-02-01"}))

    updated = list_assignments_for_version(db_path, current_version.id)[0]
    assert updated.accepted_at == date(2027, 2, 1)
    assert updated.notified_at == date(2027, 2, 1)


def test_participants_notify_only_sets_notified(db_path, monkeypatch) -> None:
    _publish_v1(db_path, monkeypatch)
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    contract.handle_post(_post(["participants", "add"], {"participant_id": "p1", "valid_from": "2027-03-01"}))
    current_version = get_contract_version_for_date(db_path, date(2021, 1, 1))
    assignment = list_assignments_for_version(db_path, current_version.id)[0]
    contract.handle_post(_post(["participants", str(assignment.id), "confirm"], {"accepted_at": "2027-02-01"}))

    contract.handle_post(_post(["participants", str(assignment.id), "notify"], {"notified_at": "2028-01-01"}))

    updated = list_assignments_for_version(db_path, current_version.id)[0]
    assert updated.accepted_at == date(2027, 2, 1)  # untouched
    assert updated.notified_at == date(2028, 1, 1)


def test_participants_depart_rejects_non_month_end(db_path, monkeypatch) -> None:
    _publish_v1(db_path, monkeypatch)
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    contract.handle_post(_post(["participants", "add"], {"participant_id": "p1", "valid_from": "2027-03-01"}))
    current_version = get_contract_version_for_date(db_path, date(2021, 1, 1))
    assignment = list_assignments_for_version(db_path, current_version.id)[0]

    with pytest.raises(FormError):
        contract.handle_post(_post(["participants", str(assignment.id), "depart"], {"end_date": "2027-05-15"}))


def test_participants_depart_sets_valid_until_and_left_at(db_path, monkeypatch) -> None:
    _publish_v1(db_path, monkeypatch)
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    contract.handle_post(_post(["participants", "add"], {"participant_id": "p1", "valid_from": "2027-03-01"}))
    current_version = get_contract_version_for_date(db_path, date(2021, 1, 1))
    assignment = list_assignments_for_version(db_path, current_version.id)[0]

    contract.handle_post(_post(["participants", str(assignment.id), "depart"], {"end_date": "2027-05-31"}))

    assert get_participant(db_path, "p1").valid_until == date(2027, 5, 31)
    updated = list_assignments_for_version(db_path, current_version.id)[0]
    assert updated.left_at == date(2027, 5, 31)


def test_declaration_view_shows_participant_and_version(db_path, monkeypatch) -> None:
    _publish_v1(db_path, monkeypatch)
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    contract.handle_post(_post(["participants", "add"], {"participant_id": "p1", "valid_from": "2027-03-01"}))
    current_version = get_contract_version_for_date(db_path, date(2021, 1, 1))
    assignment = list_assignments_for_version(db_path, current_version.id)[0]

    html = contract.handle_get(_ctx(segments=["participants", str(assignment.id), "declaration"]))
    assert "Haus 1" in html
    assert f"Version {current_version.version}" in html


def test_declaration_view_unknown_assignment_shows_placeholder(db_path) -> None:
    html = contract.handle_get(_ctx(segments=["participants", "99999", "declaration"]))
    assert "nicht gefunden" in html
