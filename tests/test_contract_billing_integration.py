# -*- coding: utf-8 -*-
"""Proves the contract-as-source-of-truth redesign needs zero changes to
shareomat.core.pipeline.leg_billing / get_tariff_for_date: publishing a
new contract version only ever creates a normal, date-scoped tariff row,
so a real compute_billing_preview() run picks the correct rate purely
based on which period it is asked to bill — before the cutover, after it,
and again after a scheduled version is withdrawn."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

import shareomat.database.contract_versions as contract_versions_module
from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.billing import compute_billing_preview
from shareomat.database.community import save_community
from shareomat.database.contract_versions import publish_version, save_draft_version, withdraw_version
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff, get_tariff_for_date
from shareomat.leg_const import METER_ROLE_CONSUMER, METER_ROLE_PRODUCER, PARTICIPANT_TYPE_CONSUMER, PARTICIPANT_TYPE_PRODUCER
from shareomat.models.community import Community
from shareomat.models.contract import ContractVersion
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    save_community(path, Community(community_id="ZEV-001", name="Test LEG"))
    create_participant(path, Participant("solar", "Solar PV", PARTICIPANT_TYPE_PRODUCER))
    create_participant(path, Participant("cons_a", "Consumer A", PARTICIPANT_TYPE_CONSUMER))
    create_meter(path, Meter("M1", "solar", "PV Meter", METER_ROLE_PRODUCER))
    create_meter(path, Meter("M2", "cons_a", "Meter A", METER_ROLE_CONSUMER))
    # The "existing tariff" publish_version() needs for grid_rate_chf_kwh inheritance —
    # simulates the one-time emergency/first-setup tariff from the Tarife page.
    create_tariff(path, Tariff(
        local_rate_chf_kwh=Decimal("0.05"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    return path


@pytest.fixture
def runtime(tmp_path):
    for name in ("inbox", "archive", "reports", "state"):
        (tmp_path / name).mkdir()
    return RuntimeConfig(paths=PathConfig(
        inbox=tmp_path / "inbox", archive=tmp_path / "archive",
        reports=tmp_path / "reports", state=tmp_path / "state",
    ))


def _fixed_today(value: date):
    class _Fixed(date):
        @classmethod
        def today(cls):
            return value
    return _Fixed


def _write_csv(directory: Path, filename: str, rows: list[tuple]) -> Path:
    path = directory / filename
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        for row in rows:
            writer.writerow(row)
    return path


def _draft(**overrides) -> ContractVersion:
    fields = dict(
        community_id="", version=0, status="draft", contract_text_snapshot="<p>Vertrag</p>",
        feed_in_rate_chf_kwh=Decimal("0.15"), admin_fee_chf_kwh=Decimal("0.005"), grid_operator="EBL",
    )
    fields.update(overrides)
    return ContractVersion(**fields)


def test_billing_uses_the_contract_rate_that_was_in_force_on_that_date(db_path, runtime, monkeypatch):
    # Heute: 01.09.2026 (per the user's own worked example).
    # v1: gültig 01.01.2026-31.12.2026, LEG-Bezug 11 Rp. — published well in advance.
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2025, 1, 1)))
    v1 = save_draft_version(db_path, _draft(local_rate_chf_kwh=Decimal("0.11"), valid_from=date(2026, 1, 1)))
    publish_version(db_path, v1.id)

    # v2: veröffentlicht 01.09.2026, gültig ab 01.01.2027, LEG-Bezug 10 Rp.
    monkeypatch.setattr(contract_versions_module, "date", _fixed_today(date(2026, 9, 1)))
    v2 = save_draft_version(db_path, _draft(local_rate_chf_kwh=Decimal("0.10"), valid_from=date(2027, 1, 1)))
    publish_version(db_path, v2.id)
    monkeypatch.setattr(contract_versions_module, "date", date)

    _write_csv(runtime.paths.inbox, "oct.csv", [
        ("2026-10-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2026-10-01T12:00:00+00:00", "M2", "0.6", "import"),
    ])
    _write_csv(runtime.paths.inbox, "jan.csv", [
        ("2027-01-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2027-01-01T12:00:00+00:00", "M2", "0.6", "import"),
    ])

    # 01.10.2026 -> Vertrag v1 -> Tarif v1 (11 Rp.)
    october_preview = compute_billing_preview(db_path, runtime, date(2026, 10, 1), date(2026, 10, 31))
    october_consumer = next(i for i in october_preview.line_items if i.participant_id == "cons_a")
    assert october_consumer.local_amount_chf == Decimal("0.066")  # 0.6 * 0.11

    # 01.01.2027 -> Vertrag v2 -> Tarif v2 (10 Rp.)
    january_preview = compute_billing_preview(db_path, runtime, date(2027, 1, 1), date(2027, 1, 31))
    january_consumer = next(i for i in january_preview.line_items if i.participant_id == "cons_a")
    assert january_consumer.local_amount_chf == Decimal("0.06")  # 0.6 * 0.10

    # Withdraw v2: v1 stays in force for January too, no dangling future tariff.
    withdraw_version(db_path, v2.id)
    assert get_tariff_for_date(db_path, date(2027, 2, 1)).local_rate_chf_kwh == Decimal("0.11")

    january_preview_after_withdraw = compute_billing_preview(db_path, runtime, date(2027, 1, 1), date(2027, 1, 31))
    consumer_after_withdraw = next(i for i in january_preview_after_withdraw.line_items if i.participant_id == "cons_a")
    assert consumer_after_withdraw.local_amount_chf == Decimal("0.066")  # back to 0.6 * 0.11
