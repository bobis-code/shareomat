# -*- coding: utf-8 -*-
"""
File: tests/test_leg_runner_persistence.py

Purpose:
    Verifies that shareomat.core.leg_runner.run(config, db_path=...) writes
    through to shareomat.database.meter_readings and
    shareomat.database.settlement_history — reusing the same seeded
    environment as tests/test_pipeline_integration.py, but focused on the
    new SQLite persistence hooks rather than the report files.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import csv
import shutil
from datetime import date
from decimal import Decimal
from pathlib import Path

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.core.leg_runner import run
from shareomat.database.config_builder import build_leg_config
from shareomat.database.community import save_community
from shareomat.database.meter_readings import list_meter_readings
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.settlement_history import list_settlement_history
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff

_METER_PROD = "CH_METER_PROD_001"
_METER_A = "CH_METER_CONS_A"


def _write_csv(path: Path, rows: list[tuple]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        for row in rows:
            writer.writerow(row)


def _make_env(root: Path):
    inbox, archive, reports, state = (root / n for n in ("inbox", "archive", "reports", "state"))
    for d in (inbox, archive, reports, state):
        d.mkdir()

    db_path = root / "shareomat.db"
    init_db(db_path)
    save_community(db_path, Community(community_id="TEST-ZEV", name="Test"))
    create_participant(db_path, Participant("solar", "Solar PV", "producer"))
    create_participant(db_path, Participant("cons_a", "Consumer A", "consumer"))
    create_meter(db_path, Meter(_METER_PROD, "solar", "PV Meter", "producer"))
    create_meter(db_path, Meter(_METER_A, "cons_a", "Meter A", "consumer"))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.10"), grid_rate_chf_kwh=Decimal("0.25"),
        feed_in_rate_chf_kwh=Decimal("0.05"), valid_from=date(2020, 1, 1),
    ))

    runtime = RuntimeConfig(paths=PathConfig(inbox=inbox, archive=archive, reports=reports, state=state))
    config = build_leg_config(db_path, runtime)
    return db_path, config, inbox, archive, state


def test_run_with_db_path_persists_meter_readings(tmp_path) -> None:
    db_path, config, inbox, _, _ = _make_env(tmp_path)
    _write_csv(inbox / "readings.csv", [
        ("2024-06-01T12:00:00+00:00", _METER_PROD, 1.0, "export"),
        ("2024-06-01T12:00:00+00:00", _METER_A, 0.3, "import"),
    ])

    run(config, db_path=db_path)

    readings = list_meter_readings(db_path)
    assert len(readings) == 2
    assert {r.meter_id for r in readings} == {_METER_PROD, _METER_A}


def test_run_with_db_path_persists_settlement_history(tmp_path) -> None:
    db_path, config, inbox, _, _ = _make_env(tmp_path)
    _write_csv(inbox / "readings.csv", [
        ("2024-06-01T12:00:00+00:00", _METER_PROD, 1.0, "export"),
        ("2024-06-01T12:00:00+00:00", _METER_A, 0.3, "import"),
    ])

    run(config, db_path=db_path)

    history = list_settlement_history(db_path)
    assert {r.participant_id for r in history} == {"solar", "cons_a"}
    cons_a = next(r for r in history if r.participant_id == "cons_a")
    assert cons_a.local_received_kwh == 0.3


def test_run_without_db_path_still_works(tmp_path) -> None:
    """run() without db_path (e.g. existing unit tests) must not break."""
    db_path, config, inbox, _, _ = _make_env(tmp_path)
    _write_csv(inbox / "readings.csv", [
        ("2024-06-01T12:00:00+00:00", _METER_PROD, 1.0, "export"),
        ("2024-06-01T12:00:00+00:00", _METER_A, 0.3, "import"),
    ])

    run(config)  # no db_path — must not raise

    assert list_meter_readings(db_path) == []  # nothing written without a db_path


def test_state_reset_with_same_file_does_not_duplicate_settlement_history(tmp_path) -> None:
    """A cleared state dir (e.g. reinstall) reprocessing the same file must not double the history."""
    db_path, config, inbox, archive, state = _make_env(tmp_path)
    _write_csv(inbox / "readings.csv", [
        ("2024-06-01T12:00:00+00:00", _METER_PROD, 1.0, "export"),
        ("2024-06-01T12:00:00+00:00", _METER_A, 0.3, "import"),
    ])

    run(config, db_path=db_path)
    assert len(list_settlement_history(db_path)) == 2

    # Simulate a state reset: same source file reappears in the inbox, is_processed() no
    # longer remembers it, but the content (and therefore the fingerprint) is identical.
    for f in state.iterdir():
        f.unlink()
    archived = next(archive.iterdir())
    shutil.copy(archived, inbox / archived.name)

    run(config, db_path=db_path)

    # settlement_cycle_records must not have doubled — same fingerprint, same period/participant
    assert len(list_settlement_history(db_path)) == 2


def test_genuinely_new_file_adds_a_new_settlement_version(tmp_path) -> None:
    """A second, different file for the same participant adds new history, not an overwrite."""
    db_path, config, inbox, _, _ = _make_env(tmp_path)
    _write_csv(inbox / "readings1.csv", [
        ("2024-06-01T12:00:00+00:00", _METER_PROD, 1.0, "export"),
        ("2024-06-01T12:00:00+00:00", _METER_A, 0.3, "import"),
    ])
    run(config, db_path=db_path)
    first_count = len(list_settlement_history(db_path))

    _write_csv(inbox / "readings2.csv", [
        ("2024-06-01T13:00:00+00:00", _METER_PROD, 1.0, "export"),
        ("2024-06-01T13:00:00+00:00", _METER_A, 0.4, "import"),
    ])
    run(config, db_path=db_path)

    assert len(list_settlement_history(db_path)) == first_count + 2  # 2 participants, new period
