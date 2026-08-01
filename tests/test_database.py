# -*- coding: utf-8 -*-
"""Tests for the SQLite admin database layer (shareomat.database.*)."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.community import get_community, save_community
from shareomat.database.config_builder import (
    IncompleteConfigError,
    build_leg_config,
    get_setup_status,
)
from shareomat.database.meters import (
    create_meter,
    get_meter,
    list_meters,
    set_meter_active,
    update_meter,
)
from shareomat.database.participants import (
    create_participant,
    get_participant,
    list_participants,
    set_participant_active,
)
from shareomat.database.settings import get_operation_settings, save_operation_settings
from shareomat.database.sqlite import connect, init_db
from shareomat.database.tariffs import create_tariff, get_tariff_for_date, list_tariffs
from shareomat.database.yaml_import import import_legacy_yaml_if_empty
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.settings import OperationSettings
from shareomat.models.tariff import Tariff


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    return path


@pytest.fixture
def runtime(tmp_path):
    return RuntimeConfig(paths=PathConfig(
        inbox=tmp_path / "inbox", archive=tmp_path / "archive",
        reports=tmp_path / "reports", state=tmp_path / "state",
    ))


# ── Schema ──────────────────────────────────────────────────────────────────


def test_init_db_creates_expected_tables(db_path):
    with connect(db_path) as conn:
        tables = {
            r["name"] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert {"communities", "participants", "meters", "tariffs", "settings", "schema_migrations"} <= tables


def test_init_db_is_idempotent(db_path):
    init_db(db_path)  # second call must not raise or duplicate schema_migrations
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM schema_migrations").fetchone()["n"]
    assert count == 1


def test_foreign_keys_are_enforced(db_path):
    with connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError):
            with conn:
                conn.execute(
                    "INSERT INTO participants (community_id, participant_id, label, "
                    "participant_type, active, created_at, updated_at) VALUES (999, 'x', 'x', 'consumer', 1, '', '')"
                )


# ── Community ───────────────────────────────────────────────────────────────


def test_community_roundtrip(db_path):
    assert get_community(db_path) is None
    save_community(db_path, Community(community_id="ZEV-001", name="Test"))
    community = get_community(db_path)
    assert community.community_id == "ZEV-001"
    assert community.name == "Test"


def test_community_save_updates_single_row(db_path):
    save_community(db_path, Community(community_id="ZEV-001", name="First"))
    save_community(db_path, Community(community_id="ZEV-001", name="Renamed"))
    with connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM communities").fetchone()["n"]
    assert count == 1
    assert get_community(db_path).name == "Renamed"


# ── Participants ────────────────────────────────────────────────────────────


def test_participant_id_unique_per_community(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "producer_consumer"))
    with pytest.raises(ValueError):
        create_participant(db_path, Participant("p1", "Duplicate", "consumer"))


def test_participant_toggle_active(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "consumer"))
    set_participant_active(db_path, "p1", False)
    assert get_participant(db_path, "p1").active is False
    set_participant_active(db_path, "p1", True)
    assert get_participant(db_path, "p1").active is True


def test_list_participants_excludes_inactive_when_requested(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "consumer", active=True))
    create_participant(db_path, Participant("p2", "Haus 2", "consumer", active=False))
    assert {p.participant_id for p in list_participants(db_path)} == {"p1", "p2"}
    assert {p.participant_id for p in list_participants(db_path, include_inactive=False)} == {"p1"}


# ── Meters ──────────────────────────────────────────────────────────────────


def test_meter_can_be_assigned_to_participant(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "producer_consumer"))
    create_meter(db_path, Meter("m1", "p1", "Hauptzähler", "producer_consumer"))
    meter = get_meter(db_path, "m1")
    assert meter.participant_id == "p1"


def test_meter_id_unique_per_community(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "producer_consumer"))
    create_meter(db_path, Meter("m1", "p1", "Zähler", "producer_consumer"))
    with pytest.raises(ValueError):
        create_meter(db_path, Meter("m1", "p1", "Duplicate", "consumer"))


def test_meter_requires_existing_participant(db_path):
    with pytest.raises(ValueError):
        create_meter(db_path, Meter("m1", "unknown", "Zähler", "consumer"))


def test_meter_reassignment_via_update(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "producer_consumer"))
    create_participant(db_path, Participant("p2", "Haus 2", "consumer"))
    create_meter(db_path, Meter("m1", "p1", "Zähler", "consumer"))
    update_meter(db_path, "m1", Meter("m1", "p2", "Zähler", "consumer"))
    assert get_meter(db_path, "m1").participant_id == "p2"


def test_meter_toggle_active(db_path):
    create_participant(db_path, Participant("p1", "Haus 1", "producer_consumer"))
    create_meter(db_path, Meter("m1", "p1", "Zähler", "consumer", active=True))
    set_meter_active(db_path, "m1", False)
    assert get_meter(db_path, "m1").active is False


# ── Tariffs (Decimal precision + date resolution) ──────────────────────────


def test_tariff_rates_survive_roundtrip_as_decimal(db_path):
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.1"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2024, 1, 1),
    ))
    tariff = list_tariffs(db_path)[0]
    assert tariff.local_rate_chf_kwh == Decimal("0.1")
    assert isinstance(tariff.local_rate_chf_kwh, Decimal)
    # 0.1 has no exact float representation — this would fail if stored as REAL/float.
    assert str(tariff.local_rate_chf_kwh) == "0.1"


def test_get_tariff_for_date_picks_the_valid_version(db_path):
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.10"), grid_rate_chf_kwh=Decimal("0.20"),
        feed_in_rate_chf_kwh=Decimal("0.05"), name="2024",
        valid_from=date(2024, 1, 1), valid_until=date(2024, 12, 31),
    ))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.15"), grid_rate_chf_kwh=Decimal("0.30"),
        feed_in_rate_chf_kwh=Decimal("0.07"), name="2025",
        valid_from=date(2025, 1, 1), valid_until=None,
    ))

    assert get_tariff_for_date(db_path, date(2024, 6, 1)).name == "2024"
    assert get_tariff_for_date(db_path, date(2025, 6, 1)).name == "2025"
    assert get_tariff_for_date(db_path, date(2023, 1, 1)) is None


# ── Settings ────────────────────────────────────────────────────────────────


def test_operation_settings_defaults_when_unset(db_path):
    settings = get_operation_settings(db_path)
    assert settings == OperationSettings()


def test_operation_settings_roundtrip(db_path):
    save_operation_settings(db_path, OperationSettings(
        unknown_meter_policy="skip", archive_processed=False,
        cron_schedule="0 6 * * *", auto_scan_enabled=True, scan_interval_seconds=30,
    ))
    settings = get_operation_settings(db_path)
    assert settings.unknown_meter_policy == "skip"
    assert settings.archive_processed is False
    assert settings.cron_schedule == "0 6 * * *"
    assert settings.auto_scan_enabled is True
    assert settings.scan_interval_seconds == 30


# ── YAML one-time import ────────────────────────────────────────────────────


def test_legacy_yaml_import_only_happens_once(db_path, tmp_path):
    yaml_path = tmp_path / "legacy.yaml"
    yaml_path.write_text(
        "leg:\n  community_id: OLD-1\n  name: Old\n"
        "participants:\n  - participant_id: p1\n    label: Haus\n    participant_type: consumer\n"
        "meters:\n  - meter_id: m1\n    participant_id: p1\n    label: Zaehler\n    role: consumer\n"
        "tariffs:\n  local_rate_chf_kwh: 0.1\n  grid_rate_chf_kwh: 0.2\n  feed_in_rate_chf_kwh: 0.05\n",
        encoding="utf-8",
    )
    assert import_legacy_yaml_if_empty(db_path, yaml_path) is True
    assert get_community(db_path).community_id == "OLD-1"

    save_community(db_path, Community(community_id="MANUALLY-CHANGED", name="x"))
    assert import_legacy_yaml_if_empty(db_path, yaml_path) is False
    assert get_community(db_path).community_id == "MANUALLY-CHANGED"


def test_fresh_yaml_without_legacy_data_is_not_imported(db_path, tmp_path):
    yaml_path = tmp_path / "fresh.yaml"
    yaml_path.write_text("paths:\n  inbox: /tmp\n  archive: /tmp\n  reports: /tmp\n  state: /tmp\n", encoding="utf-8")
    assert import_legacy_yaml_if_empty(db_path, yaml_path) is False
    assert get_community(db_path) is None


# ── config_builder ──────────────────────────────────────────────────────────


def test_empty_database_starts_in_setup_mode(db_path, runtime):
    status = get_setup_status(db_path)
    assert status.is_complete is False
    with pytest.raises(IncompleteConfigError):
        build_leg_config(db_path, runtime)


def test_complete_database_builds_valid_leg_config(db_path, runtime):
    save_community(db_path, Community(community_id="ZEV-001", name="Test"))
    create_participant(db_path, Participant("p1", "Solar", "producer"))
    create_participant(db_path, Participant("p2", "Flat", "consumer"))
    create_meter(db_path, Meter("m1", "p1", "PV", "producer"))
    create_meter(db_path, Meter("m2", "p2", "Flat meter", "consumer"))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.12"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))

    assert get_setup_status(db_path).is_complete is True
    config = build_leg_config(db_path, runtime)
    assert config.community.community_id == "ZEV-001"
    assert len(config.participants) == 2
    assert len(config.meters) == 2


def test_config_builder_excludes_meters_outside_validity_window(db_path, runtime):
    from shareomat.config import validate_leg_config

    save_community(db_path, Community(community_id="ZEV-001", name="Test"))
    create_participant(db_path, Participant("p1", "Solar", "producer"))
    create_participant(db_path, Participant("p2", "Flat", "consumer"))
    create_meter(db_path, Meter("m1", "p1", "PV", "producer"))
    # This meter's validity window has already ended — must not appear in the built config.
    create_meter(db_path, Meter("m2", "p2", "Flat meter", "consumer", valid_until=date(2000, 1, 1)))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.12"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))

    # m1 is still valid, so the config builds — but m2 (expired) must be excluded.
    config = build_leg_config(db_path, runtime)
    assert [m.meter_id for m in config.meters] == ["m1"]

    # With m2 excluded, no active consumer meter remains — validate_leg_config must catch that.
    with pytest.raises(ValueError, match="consumer"):
        validate_leg_config(config)
