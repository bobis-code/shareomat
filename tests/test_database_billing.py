# -*- coding: utf-8 -*-
"""Tests for the interactive billing workflow (shareomat.database.billing)."""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.billing import (
    BillingWorkflowError,
    cancel_billing_run,
    compute_billing_preview,
    get_billing_run,
    list_billing_runs,
    release_billing_run,
    save_draft,
)
from shareomat.database.community import save_community
from shareomat.database.config_builder import IncompleteConfigError
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.leg_const import (
    METER_ROLE_CONSUMER,
    METER_ROLE_PRODUCER,
    PARTICIPANT_TYPE_CONSUMER,
    PARTICIPANT_TYPE_PRODUCER,
)
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    return path


@pytest.fixture
def runtime(tmp_path):
    for name in ("inbox", "archive", "reports", "state"):
        (tmp_path / name).mkdir()
    return RuntimeConfig(paths=PathConfig(
        inbox=tmp_path / "inbox", archive=tmp_path / "archive",
        reports=tmp_path / "reports", state=tmp_path / "state",
    ))


def _write_csv(directory: Path, filename: str, rows: list[tuple]) -> Path:
    path = directory / filename
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        for row in rows:
            writer.writerow(row)
    return path


def _seed_basic_community(db_path: Path, *, tariff_rate: str = "0.12", valid_from: date = date(2020, 1, 1)) -> None:
    save_community(db_path, Community(community_id="ZEV-001", name="Test"))
    create_participant(db_path, Participant("solar", "Solar PV", PARTICIPANT_TYPE_PRODUCER))
    create_participant(db_path, Participant("cons_a", "Consumer A", PARTICIPANT_TYPE_CONSUMER))
    create_meter(db_path, Meter("M1", "solar", "PV Meter", METER_ROLE_PRODUCER))
    create_meter(db_path, Meter("M2", "cons_a", "Meter A", METER_ROLE_CONSUMER))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal(tariff_rate), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=valid_from,
    ))


# ── Full happy-path billing ──────────────────────────────────────────────────


def test_complete_billing_computes_local_and_grid_split(db_path, runtime):
    _seed_basic_community(db_path)
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.6", "import"),
    ])

    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))

    assert preview.availability.meters_missing_data == []
    assert preview.availability.unknown_meter_ids == []
    consumer = next(i for i in preview.line_items if i.participant_id == "cons_a")
    assert consumer.local_received_kwh == Decimal("0.6")
    assert consumer.local_amount_chf == Decimal("0.072")  # 0.6 * 0.12
    assert consumer.grid_import_kwh == Decimal("0")       # fully covered locally -> no grid draw
    assert preview.total_leg_amount_chf == Decimal("0.072")


# ── Missing data / unknown meters ───────────────────────────────────────────


def test_missing_meter_data_is_reported(db_path, runtime):
    _seed_basic_community(db_path)
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "1.0", "export"),
        # M2 (consumer meter) has no readings at all in this period.
    ])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    assert preview.availability.meters_missing_data == ["M2"]


def test_unknown_meter_is_reported_and_excluded_from_billing(db_path, runtime):
    _seed_basic_community(db_path)
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.6", "import"),
        ("2027-07-01T12:00:00+00:00", "M_GHOST", "0.3", "import"),
    ])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    assert preview.availability.unknown_meter_ids == ["M_GHOST"]
    assert all(pid != "M_GHOST" for item in preview.line_items for pid in item.meter_ids)


def test_incomplete_setup_raises_incomplete_config_error(db_path, runtime):
    # No community/participants/meters/tariff seeded at all.
    with pytest.raises(IncompleteConfigError):
        compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))


def test_end_date_before_start_date_raises(db_path, runtime):
    _seed_basic_community(db_path)
    with pytest.raises(BillingWorkflowError):
        compute_billing_preview(db_path, runtime, date(2027, 7, 31), date(2027, 7, 1))


# ── Multiple meters per participant ─────────────────────────────────────────


def test_multiple_meters_per_participant_are_summed(db_path, runtime):
    save_community(db_path, Community(community_id="ZEV-001", name="Test"))
    create_participant(db_path, Participant("solar", "Solar PV", PARTICIPANT_TYPE_PRODUCER))
    create_participant(db_path, Participant("cons_a", "Consumer A", PARTICIPANT_TYPE_CONSUMER))
    create_meter(db_path, Meter("M1", "solar", "PV Meter", METER_ROLE_PRODUCER))
    create_meter(db_path, Meter("M2", "cons_a", "Meter A", METER_ROLE_CONSUMER))
    create_meter(db_path, Meter("M3", "cons_a", "Meter B", METER_ROLE_CONSUMER))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.12"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "2.0", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.6", "import"),
        ("2027-07-01T12:00:00+00:00", "M3", "0.4", "import"),
    ])

    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    consumer = next(i for i in preview.line_items if i.participant_id == "cons_a")
    assert set(consumer.meter_ids) == {"M2", "M3"}
    assert consumer.local_received_kwh == Decimal("1.0")  # 0.6 + 0.4 across both meters


# ── Tariff validity window ──────────────────────────────────────────────────


def test_tariff_validity_window_picks_correct_rate(db_path, runtime):
    save_community(db_path, Community(community_id="ZEV-001", name="Test"))
    create_participant(db_path, Participant("solar", "Solar PV", PARTICIPANT_TYPE_PRODUCER))
    create_participant(db_path, Participant("cons_a", "Consumer A", PARTICIPANT_TYPE_CONSUMER))
    create_meter(db_path, Meter("M1", "solar", "PV Meter", METER_ROLE_PRODUCER))
    create_meter(db_path, Meter("M2", "cons_a", "Meter A", METER_ROLE_CONSUMER))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.10"), grid_rate_chf_kwh=Decimal("0.20"),
        feed_in_rate_chf_kwh=Decimal("0.05"), name="2026",
        valid_from=date(2026, 1, 1), valid_until=date(2026, 12, 31),
    ))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.15"), grid_rate_chf_kwh=Decimal("0.30"),
        feed_in_rate_chf_kwh=Decimal("0.07"), name="2027",
        valid_from=date(2027, 1, 1),
    ))
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.6", "import"),
    ])

    preview_2027 = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    assert preview_2027.tariff_name == "2027"
    assert preview_2027.local_rate_chf_kwh == Decimal("0.15")


# ── Immutability ─────────────────────────────────────────────────────────────


def test_saved_snapshot_is_unaffected_by_later_tariff_change(db_path, runtime):
    _seed_basic_community(db_path, tariff_rate="0.12")
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.6", "import"),
    ])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    run = save_draft(db_path, preview)

    # Change the LEG tariff after saving.
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.99"), grid_rate_chf_kwh=Decimal("0.99"),
        feed_in_rate_chf_kwh=Decimal("0.99"), valid_from=date(2020, 1, 1),
    ))

    reloaded = get_billing_run(db_path, run.id)
    consumer = next(i for i in reloaded.line_items if i.participant_id == "cons_a")
    assert consumer.local_amount_chf == Decimal("0.072")  # still the OLD 0.12 rate, not 0.99
    assert reloaded.run.local_rate_chf_kwh == Decimal("0.12")


def test_released_run_cannot_be_released_again_or_re_saved_over(db_path, runtime):
    _seed_basic_community(db_path)
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.6", "import"),
    ])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    run = save_draft(db_path, preview)
    release_billing_run(db_path, run.id)

    with pytest.raises(BillingWorkflowError):
        release_billing_run(db_path, run.id)

    # Saving a new draft for the SAME period must create a new version, not touch the released one.
    run2 = save_draft(db_path, preview)
    assert run2.id != run.id
    assert run2.version == run.version + 1
    assert run2.billing_period_id == run.billing_period_id

    still_released = get_billing_run(db_path, run.id)
    assert still_released.run.status == "released"


def test_cannot_cancel_an_already_cancelled_run(db_path, runtime):
    _seed_basic_community(db_path)
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.6", "import"),
    ])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    run = save_draft(db_path, preview)
    cancel_billing_run(db_path, run.id)
    with pytest.raises(BillingWorkflowError):
        cancel_billing_run(db_path, run.id)


def test_release_unknown_run_raises(db_path):
    with pytest.raises(BillingWorkflowError):
        release_billing_run(db_path, 9999)


# ── Decimal rounding ─────────────────────────────────────────────────────────


def test_rounding_uses_decimal_not_float_artifacts(db_path, runtime):
    save_community(db_path, Community(community_id="ZEV-001", name="Test"))
    create_participant(db_path, Participant("solar", "Solar PV", PARTICIPANT_TYPE_PRODUCER))
    create_participant(db_path, Participant("cons_a", "Consumer A", PARTICIPANT_TYPE_CONSUMER))
    create_meter(db_path, Meter("M1", "solar", "PV Meter", METER_ROLE_PRODUCER))
    create_meter(db_path, Meter("M2", "cons_a", "Meter A", METER_ROLE_CONSUMER))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.1"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))
    # 1/3 kWh at 0.1 CHF/kWh is a classic float-imprecision trap.
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "0.333333", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.333333", "import"),
    ])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    consumer = next(i for i in preview.line_items if i.participant_id == "cons_a")
    assert isinstance(consumer.local_amount_chf, Decimal)
    # Rounded to 4 decimals, no float noise like Decimal('0.03333329999999...').
    assert str(consumer.local_amount_chf) == "0.0333"


# ── Restart / reload from SQLite ────────────────────────────────────────────


def test_billing_run_survives_reload_from_a_fresh_connection(db_path, runtime):
    _seed_basic_community(db_path)
    _write_csv(runtime.paths.inbox, "readings.csv", [
        ("2027-07-01T12:00:00+00:00", "M1", "1.0", "export"),
        ("2027-07-01T12:00:00+00:00", "M2", "0.6", "import"),
    ])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    run = save_draft(db_path, preview)
    release_billing_run(db_path, run.id)

    # Simulate "process restart": look the run up again via a brand new query,
    # touching no in-memory state from the calls above.
    runs = list_billing_runs(db_path)
    assert len(runs) == 1
    assert runs[0].id == run.id
    assert runs[0].status == "released"

    reloaded = get_billing_run(db_path, run.id)
    assert len(reloaded.line_items) == 2
    assert len(reloaded.sources) == 1
    assert reloaded.sources[0].filename == "readings.csv"
