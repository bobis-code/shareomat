# -*- coding: utf-8 -*-
"""Tests for the "Vertrag" admin page (shareomat.web.pages.contract)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import shareomat.database.contract_versions as contract_versions_module
from shareomat.database.community import save_community
from shareomat.database.contract_versions import get_contract_version, list_contract_versions
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
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
    "local_rate_chf_kwh": "0.11",
    "feed_in_rate_chf_kwh": "0.15",
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


def test_draft_update_edits_in_place(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    contract.handle_post(_post(["draft", "create"], _PRICE_FORM))
    draft = list_contract_versions(db_path)[0]

    updated_form = dict(_PRICE_FORM, local_rate_chf_kwh="0.20", version_id=str(draft.id))
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
    contract.handle_post(_post(["draft", "create"], dict(_PRICE_FORM, local_rate_chf_kwh="0.13")))
    source = list_contract_versions(db_path)[0]

    html = contract.handle_get(_ctx(query={"copy_from": str(source.id)}))
    assert "0.13" in html
    assert "Neuen Entwurf erstellen" in html  # copying always creates a NEW draft, never edits the source


def test_edit_prefills_form_for_existing_draft(db_path) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test LEG"))
    contract.handle_post(_post(["draft", "create"], dict(_PRICE_FORM, local_rate_chf_kwh="0.13")))
    draft = list_contract_versions(db_path)[0]

    html = contract.handle_get(_ctx(query={"edit": str(draft.id)}))
    assert "Entwurf bearbeiten" in html
    assert "0.13" in html


def test_unknown_post_action_raises(db_path) -> None:
    with pytest.raises(FormError):
        contract.handle_post(_post(["nonsense"]))
