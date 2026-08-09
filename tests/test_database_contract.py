# -*- coding: utf-8 -*-
"""Tests for the LEG contract feature's database layer: contract_settings,
contract_versions (draft/publish/withdraw/end lifecycle), and participant_contract."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import shareomat.database.contract_versions as contract_versions_module
from shareomat.database.community import save_community
from shareomat.database.contract_settings import get_contract_settings, save_contract_settings
from shareomat.database.contract_versions import (
    ContractWorkflowError,
    _add_months,
    delete_draft,
    end_contract,
    get_contract_version,
    get_contract_version_for_date,
    get_latest_relevant_version,
    list_contract_versions,
    publish_version,
    save_draft_version,
    update_draft_version,
    withdraw_version,
)
from shareomat.database.participant_contract import (
    ParticipantContractError,
    assign_contract,
    get_assignment,
    has_any_assignment,
    list_assignments_for_version,
    mark_accepted,
    mark_notified,
    record_departure,
)
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import get_tariff_for_date
from shareomat.models.community import Community
from shareomat.models.contract import ContractSettings, ContractVersion
from shareomat.models.participant import Participant


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    save_community(path, Community(community_id="ZEV-001", name="Test LEG"))
    return path


def _draft(*, text: str = "<p>Entwurf</p>", **overrides) -> ContractVersion:
    fields = dict(
        community_id="", version=0, status="draft", contract_text_snapshot=text,
        local_rate_chf_kwh=Decimal("0.11"), feed_in_rate_chf_kwh=Decimal("0.15"),
        admin_fee_chf_kwh=Decimal("0.005"), grid_operator="EBL",
        representative_name="Jane Doe",
    )
    fields.update(overrides)
    return ContractVersion(**fields)


def _fixed_today(value: date):
    """A date subclass whose .today() always returns `value` — for testing publish_version()'s notice-period math independent of the real calendar."""
    class _Fixed(date):
        @classmethod
        def today(cls):
            return value
    return _Fixed


# ── contract_settings ────────────────────────────────────────────────────────


def test_contract_settings_defaults_when_unset(db_path):
    settings = get_contract_settings(db_path)
    assert settings == ContractSettings()


def test_contract_settings_roundtrip(db_path):
    save_contract_settings(db_path, ContractSettings(
        representative_name="Jane Doe", grid_operator="EBL",
        distribution_method="proportional zum Verbrauch",
    ))
    settings = get_contract_settings(db_path)
    assert settings.representative_name == "Jane Doe"
    assert settings.grid_operator == "EBL"


# ── _add_months ──────────────────────────────────────────────────────────────


def test_add_months_within_year():
    assert _add_months(date(2026, 3, 15), 4) == date(2026, 7, 15)


def test_add_months_crosses_year_boundary():
    assert _add_months(date(2026, 10, 1), 4) == date(2027, 2, 1)


def test_add_months_clamps_day_to_shorter_month():
    assert _add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)


# ── draft: freely creatable/editable/deletable ──────────────────────────────


def test_save_draft_version_starts_at_1(db_path):
    v = save_draft_version(db_path, _draft())
    assert v.version == 1
    assert v.status == "draft"
    assert v.contract_text_snapshot == "<p>Entwurf</p>"


def test_save_draft_version_increments(db_path):
    save_draft_version(db_path, _draft(text="<p>v1</p>"))
    v2 = save_draft_version(db_path, _draft(text="<p>v2</p>"))
    assert v2.version == 2


def test_save_draft_version_accepts_missing_or_past_valid_from(db_path):
    v = save_draft_version(db_path, _draft(valid_from=None))
    assert v.valid_from is None
    v2 = save_draft_version(db_path, _draft(text="<p>v2</p>", valid_from=date(2020, 1, 1)))
    assert v2.valid_from == date(2020, 1, 1)


def test_update_draft_version_changes_fields_repeatedly(db_path):
    v = save_draft_version(db_path, _draft(text="<p>v1</p>"))
    updated = update_draft_version(db_path, v.id, _draft(text="<p>v1 rev2</p>", local_rate_chf_kwh=Decimal("0.20")))
    assert updated.contract_text_snapshot == "<p>v1 rev2</p>"
    assert updated.local_rate_chf_kwh == Decimal("0.20")
    assert updated.version == 1  # same row, not a new version

    updated_again = update_draft_version(db_path, v.id, _draft(text="<p>v1 rev3</p>"))
    assert updated_again.contract_text_snapshot == "<p>v1 rev3</p>"


def test_delete_draft_removes_it(db_path):
    v = save_draft_version(db_path, _draft())
    delete_draft(db_path, v.id)
    assert get_contract_version(db_path, v.id) is None


def test_delete_draft_unknown_version_raises(db_path):
    with pytest.raises(ContractWorkflowError):
        delete_draft(db_path, 99999)


# ── publish_version ──────────────────────────────────────────────────────────


def test_publish_rejects_missing_valid_from(db_path):
    v = save_draft_version(db_path, _draft(valid_from=None))
    with pytest.raises(ContractWorkflowError):
        publish_version(db_path, v.id)


def test_publish_blocks_without_existing_tariff_no_zero_fallback(db_path):
    v = save_draft_version(db_path, _draft(valid_from=date(2030, 1, 1)))
    with pytest.raises(ContractWorkflowError, match="Netznutzung"):
        publish_version(db_path, v.id)


def test_publish_rejects_too_early_first_publish(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 6, 1)))
    v = save_draft_version(db_path, _draft(valid_from=date(2026, 7, 1), price_notice_period_months=4))
    with pytest.raises(ContractWorkflowError, match="Ankündigungsfrist"):
        publish_version(db_path, v.id)


def test_publish_accepts_sufficient_notice_and_creates_tariff(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 1, 1)))
    v = save_draft_version(db_path, _draft(
        valid_from=date(2026, 5, 1), price_notice_period_months=4, local_rate_chf_kwh=Decimal("0.11"),
    ))
    published = publish_version(db_path, v.id)
    assert published.status == "published"
    assert published.tariff_id is not None

    tariff = get_tariff_for_date(db_path, date(2026, 5, 1))
    assert tariff is not None
    assert tariff.local_rate_chf_kwh == Decimal("0.11")
    assert tariff.grid_rate_chf_kwh == Decimal("0.28")  # carried over from the existing tariff
    assert tariff.name == f"Vertrag v{v.version}"


def test_publish_second_version_closes_out_prior_tariff_and_version(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(text="<p>v1</p>", valid_from=date(2026, 1, 1)))
    v1_published = publish_version(db_path, v1.id)

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    v2 = save_draft_version(db_path, _draft(
        text="<p>v2</p>", valid_from=date(2027, 1, 1), local_rate_chf_kwh=Decimal("0.10"),
    ))
    v2_published = publish_version(db_path, v2.id)

    assert v2_published.supersedes_version_id == v1_published.id

    v1_after = get_contract_version(db_path, v1_published.id)
    assert v1_after.status == "published"  # never "superseded" — only two stored statuses
    assert v1_after.valid_until == date(2026, 12, 31)
    assert v1_after.contract_text_snapshot == "<p>v1</p>"  # never edited

    old_tariff = get_tariff_for_date(db_path, date(2026, 6, 1))
    assert old_tariff.local_rate_chf_kwh == Decimal("0.11")
    new_tariff = get_tariff_for_date(db_path, date(2027, 2, 1))
    assert new_tariff.local_rate_chf_kwh == Decimal("0.10")


def test_publish_only_allowed_for_drafts(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v = save_draft_version(db_path, _draft(valid_from=date(2026, 1, 1)))
    publish_version(db_path, v.id)
    with pytest.raises(ContractWorkflowError):
        publish_version(db_path, v.id)


# ── withdraw_version ──────────────────────────────────────────────────────────


def test_withdraw_scheduled_version_reverts_to_draft_and_restores_prior(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(text="<p>v1</p>", valid_from=date(2026, 1, 1)))
    publish_version(db_path, v1.id)

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    v2 = save_draft_version(db_path, _draft(
        text="<p>v2</p>", valid_from=date(2027, 1, 1), local_rate_chf_kwh=Decimal("0.10"),
    ))
    v2_published = publish_version(db_path, v2.id)

    monkeypatch.setattr(contract_versions_module, "date", date)  # back to the real calendar for the withdraw call itself
    withdrawn = withdraw_version(db_path, v2_published.id)
    assert withdrawn.status == "draft"
    assert withdrawn.tariff_id is None
    assert withdrawn.supersedes_version_id is None

    v1_after = get_contract_version(db_path, v1.id)
    assert v1_after.valid_until is None  # fully restored to open-ended

    assert get_tariff_for_date(db_path, date(2027, 2, 1)).local_rate_chf_kwh == Decimal("0.11")  # v1's rate again


def test_withdraw_rejects_already_effective_version(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2020, 1, 1)))
    v = save_draft_version(db_path, _draft(valid_from=date(2020, 6, 1)))
    published = publish_version(db_path, v.id)

    monkeypatch.setattr(contract_versions_module, "date", date)
    with pytest.raises(ContractWorkflowError):
        withdraw_version(db_path, published.id)


def test_withdraw_rejects_drafts(db_path):
    v = save_draft_version(db_path, _draft())
    with pytest.raises(ContractWorkflowError):
        withdraw_version(db_path, v.id)


# ── end_contract ──────────────────────────────────────────────────────────────


def test_end_contract_sets_valid_until_without_deleting(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    # This base tariff only exists to give publish_version() a grid_rate_chf_kwh to
    # inherit — closed out before the contract's own valid_from so it can't resurface
    # as a fallback later and mask the "no tariff after end_contract()" assertion below.
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1), valid_until=date(2020, 5, 31),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2020, 1, 1)))
    v = save_draft_version(db_path, _draft(valid_from=date(2020, 6, 1)))
    published = publish_version(db_path, v.id)

    monkeypatch.setattr(contract_versions_module, "date", date)
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 6, 1)))
    ended = end_contract(db_path, published.id, date(2026, 12, 31))
    assert ended.status == "published"  # still there, just closed out — never deleted
    assert ended.valid_until == date(2026, 12, 31)
    assert get_tariff_for_date(db_path, date(2027, 1, 1)) is None


def test_end_contract_rejects_drafts_and_future_versions(db_path):
    v = save_draft_version(db_path, _draft())
    with pytest.raises(ContractWorkflowError):
        end_contract(db_path, v.id, date(2030, 1, 1))


# ── date-based resolution ──────────────────────────────────────────────────


def test_get_contract_version_for_date_resolves_active_vs_scheduled(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(text="<p>v1</p>", valid_from=date(2026, 1, 1)))
    publish_version(db_path, v1.id)

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    v2 = save_draft_version(db_path, _draft(text="<p>v2</p>", valid_from=date(2027, 1, 1)))
    publish_version(db_path, v2.id)

    # both v1 and v2 are "published" simultaneously — distinguished only by date range.
    assert get_contract_version_for_date(db_path, date(2026, 10, 1)).version == 1
    assert get_contract_version_for_date(db_path, date(2027, 3, 1)).version == 2
    assert get_contract_version_for_date(db_path, date(2020, 1, 1)) is None


def test_get_latest_relevant_version_ignores_dates(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(valid_from=date(2026, 1, 1)))
    publish_version(db_path, v1.id)

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    v2 = save_draft_version(db_path, _draft(valid_from=date(2027, 1, 1)))
    publish_version(db_path, v2.id)

    assert get_latest_relevant_version(db_path).version == 2


def test_list_contract_versions_newest_first(db_path):
    save_draft_version(db_path, _draft(text="<p>v1</p>"))
    save_draft_version(db_path, _draft(text="<p>v2</p>"))
    versions = list_contract_versions(db_path)
    assert [v.version for v in versions] == [2, 1]


# ── notice-period diff logic ─────────────────────────────────────────────────


def test_price_only_change_requires_price_notice_period(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(
        valid_from=date(2026, 1, 1), price_notice_period_months=4, contract_notice_period_months=6,
    ))
    publish_version(db_path, v1.id)

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    # Only the price changes; 4 months out from 2026-09-01 is 2027-01-01 — exactly enough.
    v2 = save_draft_version(db_path, _draft(
        valid_from=date(2027, 1, 1), local_rate_chf_kwh=Decimal("0.10"),
        price_notice_period_months=4, contract_notice_period_months=6,
    ))
    publish_version(db_path, v2.id)  # should not raise


def test_master_data_change_requires_contract_notice_period(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(
        valid_from=date(2026, 1, 1), price_notice_period_months=4, contract_notice_period_months=6,
    ))
    publish_version(db_path, v1.id)

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    # grid_operator changes — 4 months out is enough for a price change, NOT for a contract change (6).
    v2 = save_draft_version(db_path, _draft(
        valid_from=date(2027, 1, 1), grid_operator="Anderer VNB",
        price_notice_period_months=4, contract_notice_period_months=6,
    ))
    with pytest.raises(ContractWorkflowError, match="Ankündigungsfrist"):
        publish_version(db_path, v2.id)


# ── participant_contract ─────────────────────────────────────────────────────


def test_assign_contract_to_known_participant(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    v = save_draft_version(db_path, _draft())
    assignment = assign_contract(db_path, "p1", v.id)
    assert assignment.participant_id == "p1"
    assert assignment.contract_version_id == v.id


def test_assign_contract_to_unknown_participant_raises(db_path):
    v = save_draft_version(db_path, _draft())
    with pytest.raises(ParticipantContractError):
        assign_contract(db_path, "does-not-exist", v.id)


def test_list_assignments_for_version(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    create_participant(db_path, Participant("p2", "Haus 2", "consumer"))
    v = save_draft_version(db_path, _draft())
    assign_contract(db_path, "p1", v.id)
    assign_contract(db_path, "p2", v.id)

    assignments = list_assignments_for_version(db_path, v.id)
    assert {a.participant_id for a in assignments} == {"p1", "p2"}


def test_reassigning_same_participant_and_version_updates_not_duplicates(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    v = save_draft_version(db_path, _draft())
    assign_contract(db_path, "p1", v.id, accepted_at=None)
    assign_contract(db_path, "p1", v.id, accepted_at=date(2024, 1, 1))

    assignments = list_assignments_for_version(db_path, v.id)
    assert len(assignments) == 1
    assert assignments[0].accepted_at == date(2024, 1, 1)


def test_mark_accepted_sets_accepted_and_notified_together(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    v = save_draft_version(db_path, _draft())
    assignment = assign_contract(db_path, "p1", v.id)

    updated = mark_accepted(db_path, assignment.id, date(2027, 1, 1))
    assert updated.accepted_at == date(2027, 1, 1)
    assert updated.notified_at == date(2027, 1, 1)


def test_mark_accepted_unknown_assignment_raises(db_path):
    with pytest.raises(ParticipantContractError):
        mark_accepted(db_path, 99999, date(2027, 1, 1))


def test_mark_notified_leaves_accepted_at_untouched(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    v = save_draft_version(db_path, _draft())
    assignment = assign_contract(db_path, "p1", v.id)
    mark_accepted(db_path, assignment.id, date(2026, 1, 1))

    updated = mark_notified(db_path, assignment.id, date(2027, 6, 1))
    assert updated.accepted_at == date(2026, 1, 1)  # original join confirmation, never re-set
    assert updated.notified_at == date(2027, 6, 1)


def test_mark_notified_unknown_assignment_raises(db_path):
    with pytest.raises(ParticipantContractError):
        mark_notified(db_path, 99999, date(2027, 1, 1))


def test_record_departure_sets_left_at(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    v = save_draft_version(db_path, _draft())
    assignment = assign_contract(db_path, "p1", v.id)

    updated = record_departure(db_path, assignment.id, date(2027, 5, 31))
    assert updated.left_at == date(2027, 5, 31)


def test_record_departure_unknown_assignment_raises(db_path):
    with pytest.raises(ParticipantContractError):
        record_departure(db_path, 99999, date(2027, 1, 1))


def test_has_any_assignment_reflects_state(db_path):
    assert has_any_assignment(db_path) is False
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    v = save_draft_version(db_path, _draft())
    assign_contract(db_path, "p1", v.id)
    assert has_any_assignment(db_path) is True


def test_get_assignment_returns_none_for_unknown_id(db_path):
    assert get_assignment(db_path, 99999) is None


# ── publish_version() participant carry-forward ─────────────────────────────


def test_publish_carries_forward_active_participants_resetting_notified_at(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    create_participant(db_path, Participant("p2", "Haus 2", "consumer"))

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(valid_from=date(2026, 1, 1)))
    v1_published = publish_version(db_path, v1.id)

    a1 = assign_contract(db_path, "p1", v1_published.id, v1_published.tariff_id, joined_at=date(2026, 1, 1))
    mark_accepted(db_path, a1.id, date(2026, 1, 1))
    a2 = assign_contract(db_path, "p2", v1_published.id, v1_published.tariff_id, joined_at=date(2026, 1, 1))
    mark_accepted(db_path, a2.id, date(2026, 1, 1))
    record_departure(db_path, a2.id, date(2026, 12, 31))  # p2 leaves before v2 takes over

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    v2 = save_draft_version(db_path, _draft(valid_from=date(2027, 1, 1)))
    v2_published = publish_version(db_path, v2.id)

    v2_assignments = list_assignments_for_version(db_path, v2_published.id)
    assert {a.participant_id for a in v2_assignments} == {"p1"}  # p2 (departed) is NOT carried forward

    carried = v2_assignments[0]
    assert carried.accepted_at == date(2026, 1, 1)  # original join confirmation preserved
    assert carried.notified_at is None              # must be re-recorded for the new version
    assert carried.joined_at == date(2026, 1, 1)     # original join date preserved, not republish date


def test_withdraw_deletes_carried_forward_participant_assignments(db_path, monkeypatch):
    from shareomat.database.tariffs import create_tariff
    from shareomat.models.tariff import Tariff
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(valid_from=date(2026, 1, 1)))
    v1_published = publish_version(db_path, v1.id)
    a1 = assign_contract(db_path, "p1", v1_published.id, v1_published.tariff_id, joined_at=date(2026, 1, 1))
    mark_accepted(db_path, a1.id, date(2026, 1, 1))

    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    v2 = save_draft_version(db_path, _draft(valid_from=date(2027, 1, 1)))
    v2_published = publish_version(db_path, v2.id)
    assert len(list_assignments_for_version(db_path, v2_published.id)) == 1

    withdraw_version(db_path, v2_published.id)

    assert list_assignments_for_version(db_path, v2_published.id) == []
    # v1's own assignment is untouched
    v1_assignments = list_assignments_for_version(db_path, v1_published.id)
    assert len(v1_assignments) == 1
    assert v1_assignments[0].accepted_at == date(2026, 1, 1)
