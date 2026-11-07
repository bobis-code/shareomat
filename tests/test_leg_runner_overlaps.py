# -*- coding: utf-8 -*-
"""
File: tests/test_leg_runner_overlaps.py

Purpose:
    Regressions for overlapping complete interval measurements in the automatic
    file runner, including persistence, MQTT output and retry after conflicts.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import csv
import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.core.collector.leg_import import scan_inbox
from shareomat.core.collector.leg_storage import is_processed
from shareomat.core.leg_runner import run
from shareomat.database.community import save_community
from shareomat.database.config_builder import build_leg_config
from shareomat.database.meter_readings import list_meter_readings
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.settlement_history import list_settlement_history
from shareomat.database.sqlite import connect, init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff

_T0 = "2026-11-03T12:00:00+00:00"
_T1 = "2026-11-04T12:00:00+00:00"
_PRODUCER = "TEST_PRODUCER"
_CONSUMER = "TEST_CONSUMER"
_OTHER = "TEST_OTHER"


def _row(meter, value, direction="import", timestamp=_T0, quality="valid"):
    return timestamp, meter, value, direction, quality


def _write(env, name, rows):
    path = env.config.paths.inbox / name
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction", "quality"])
        writer.writerows(rows)
    return path


def _outputs(env):
    """Capture prior outputs, including an existing successful cycle, without the inbox."""
    paths = env.config.paths
    files = {
        f"{directory.name}/{path.name}": path.read_bytes()
        for directory in (paths.archive, paths.reports, paths.state)
        for path in directory.iterdir() if path.is_file()
    }
    with connect(env.db) as conn:
        tables = {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]
            for table in ("meter_readings", "settlement_cycle_records", "billing_runs", "billing_records")
        }
    return files, tables


def _consumer_report(env):
    report = next(env.config.paths.reports.glob("billing_*.json"))
    records = json.loads(report.read_text(encoding="utf-8"))
    return next(record for record in records if record["participant_id"] == "consumer")


@pytest.fixture
def env(tmp_path):
    paths = PathConfig(**{name: tmp_path / name for name in ("inbox", "archive", "reports", "state")})
    for directory in (paths.inbox, paths.archive, paths.reports, paths.state):
        directory.mkdir()
    db = tmp_path / "test.db"
    init_db(db)
    save_community(db, Community(community_id="TEST_LEG", name="Synthetic community"))
    for pid, meter, role in (
        ("producer", _PRODUCER, "producer"),
        ("consumer", _CONSUMER, "producer_consumer"),
        ("other", _OTHER, "consumer"),
    ):
        create_participant(db, Participant(pid, pid))
        create_meter(db, Meter(meter, pid, meter, role))
    create_tariff(db, Tariff(
        local_rate_chf_kwh=Decimal("0.20"), grid_rate_chf_kwh=Decimal("0.30"),
        feed_in_rate_chf_kwh=Decimal("0.20"), valid_from=date(2020, 1, 1),
    ))
    config = build_leg_config(db, RuntimeConfig(paths=paths))
    return SimpleNamespace(db=db, config=config, mqtt=Mock())


@pytest.mark.parametrize("monthly", [False, True], ids=["identical-files", "day-and-month"])
def test_overlapping_files_count_each_complete_interval_once(env, monthly):
    rows = [_row(_PRODUCER, 2, "export"), _row(_CONSUMER, 1)]
    first = _write(env, "a-day.csv", rows)
    second_rows = rows + ([_row(_CONSUMER, 0, timestamp=_T1)] if monthly else [])
    second = _write(env, "b-month.csv", second_rows)
    original = {path.name: path.read_bytes() for path in (first, second)}
    imports = scan_inbox(env.config.paths.inbox)

    run(env.config, mqtt_client=env.mqtt, db_path=env.db)

    report = _consumer_report(env)
    assert report["total_import_kwh"] == 1.0
    assert report["local_received_kwh"] == 1.0
    assert report["local_cost_chf"] == 0.20
    assert len(list_meter_readings(env.db)) == (3 if monthly else 2)
    consumer = next(r for r in list_settlement_history(env.db) if r.participant_id == "consumer")
    assert consumer.local_received_kwh == 1.0
    assert consumer.local_cost_chf == Decimal("0.20")
    base = f"{env.config.mqtt.topic_prefix}/billing/consumer"
    env.mqtt.publish.assert_any_call(f"{base}/local_share_kwh", "1.0",
                                     qos=env.config.mqtt.qos, retain=env.config.mqtt.retain)
    env.mqtt.publish.assert_any_call(f"{base}/local_cost_chf", "0.2",
                                     qos=env.config.mqtt.qos, retain=env.config.mqtt.retain)
    assert set(report["source_files"]) == set(original)
    assert not list(env.config.paths.inbox.iterdir())
    assert all(is_processed(env.config.paths.state, imp.sha256) for imp in imports)
    assert {p.name: p.read_bytes() for p in env.config.paths.archive.iterdir()} == original


@pytest.mark.parametrize("case", ["single-file", "separate-meters", "separate-directions", "disjoint-slots"])
def test_distinct_measurements_keep_their_energy_and_normal_costs(env, case):
    first = [_row(_PRODUCER, 4, "export"), _row(_CONSUMER, 1)]
    second = []
    if case == "separate-meters":
        second = [_row(_OTHER, 1)]  # Same slot, direction and value, different physical meter.
    elif case == "separate-directions":
        second = [_row(_CONSUMER, 1, "export")]
    elif case == "disjoint-slots":
        second = [_row(_PRODUCER, 4, "export", _T1), _row(_CONSUMER, 2, timestamp=_T1)]
    _write(env, "a.csv", first)
    if second:
        _write(env, "b.csv", second)

    run(env.config, db_path=env.db)

    readings = list_meter_readings(env.db)
    assert len(readings) == len(first) + len(second)
    assert sum(r.value_kwh for r in readings if r.direction == "import") == (
        3 if case == "disjoint-slots" else 2 if case == "separate-meters" else 1
    )
    report = _consumer_report(env)
    assert report["local_received_kwh"] == (3 if case == "disjoint-slots" else 1)
    assert report["local_cost_chf"] == (0.60 if case == "disjoint-slots" else 0.20)
    assert report["total_export_kwh"] == (1 if case == "separate-directions" else 0)


@pytest.mark.parametrize("second_value,second_quality", [
    (1.5, "valid"), (1.0000000001, "valid"), (1, "invalid"),
], ids=["different-value", "small-difference", "different-quality"])
def test_conflicting_files_leave_no_outputs_or_processed_files(env, second_value, second_quality):
    first = _write(env, "a-day.csv", [_row(_PRODUCER, 4, "export"), _row(_CONSUMER, 1)])
    second = _write(env, "b-month.csv", [_row(_CONSUMER, second_value, quality=second_quality)])
    original = {p.name: p.read_bytes() for p in (first, second)}
    before = _outputs(env)

    with pytest.raises(ValueError) as error:
        run(env.config, mqtt_client=env.mqtt, db_path=env.db)

    message = str(error.value)
    assert "Widersprüchliche Intervallmessung" in message
    assert _CONSUMER in message and "import" in message and "2026-11-03" in message
    assert first.name in message and second.name in message
    assert _outputs(env) == before
    assert {p.name: p.read_bytes() for p in env.config.paths.inbox.iterdir()} == original
    env.mqtt.publish.assert_not_called()


def test_corrected_conflict_can_be_retried_without_resetting_state(env):
    _write(env, "a-day.csv", [_row(_PRODUCER, 4, "export"), _row(_CONSUMER, 1)])
    _write(env, "b-month.csv", [_row(_CONSUMER, 1.5)])
    with pytest.raises(ValueError):
        run(env.config, mqtt_client=env.mqtt, db_path=env.db)
    _write(env, "b-month.csv", [_row(_CONSUMER, 1)])

    run(env.config, mqtt_client=env.mqtt, db_path=env.db)

    assert _consumer_report(env)["local_received_kwh"] == 1.0
    assert not list(env.config.paths.inbox.iterdir())
    assert len(list(env.config.paths.archive.iterdir())) == 2


@pytest.mark.parametrize("already_processed_in_inbox", [False, True])
def test_conflict_does_not_change_a_previous_successful_cycle(env, already_processed_in_inbox):
    _write(env, "previous.csv", [_row(_PRODUCER, 4, "export", _T1), _row(_CONSUMER, 2, timestamp=_T1)])
    run(env.config, db_path=env.db)
    before = _outputs(env)
    if already_processed_in_inbox:
        archived = env.config.paths.archive / "previous.csv"
        (env.config.paths.inbox / archived.name).write_bytes(archived.read_bytes())
    _write(env, "a.csv", [_row(_CONSUMER, 1)])
    _write(env, "b.csv", [_row(_CONSUMER, 1.5)])

    with pytest.raises(ValueError):
        run(env.config, mqtt_client=env.mqtt, db_path=env.db)

    assert _outputs(env) == before
    if already_processed_in_inbox:
        assert (env.config.paths.inbox / archived.name).read_bytes() == archived.read_bytes()
    env.mqtt.publish.assert_not_called()


def test_conflicting_rows_within_one_csv_cannot_publish_a_partial_result(env):
    _write(env, "conflict.csv", [
        _row(_PRODUCER, 4, "export"), _row(_CONSUMER, 1), _row(_CONSUMER, 1.5),
    ])
    before = _outputs(env)

    with pytest.raises(ValueError, match="widersprüchliche Doppelzeile"):
        run(env.config, mqtt_client=env.mqtt, db_path=env.db)

    assert _outputs(env) == before
    env.mqtt.publish.assert_not_called()


def test_same_instant_with_different_utc_offsets_counts_once(env):
    _write(env, "a.csv", [_row(_PRODUCER, 2, "export"), _row(_CONSUMER, 1)])
    _write(env, "b.csv", [_row(_CONSUMER, 1, timestamp="2026-11-03T13:00:00+01:00")])

    run(env.config, db_path=env.db)

    assert _consumer_report(env)["local_received_kwh"] == 1.0
    assert len(list_meter_readings(env.db)) == 2


def test_identical_rows_in_one_csv_keep_existing_behavior(env):
    _write(env, "one.csv", [_row(_PRODUCER, 2, "export"), _row(_CONSUMER, 1), _row(_CONSUMER, 1)])

    run(env.config, db_path=env.db)

    assert _consumer_report(env)["local_received_kwh"] == 1.0
    assert len(list_meter_readings(env.db)) == 2


@pytest.mark.parametrize("unreadable", [False, True], ids=["rejected-csv-row", "csv-parser-exception"])
def test_parse_errors_cannot_turn_other_files_into_a_successful_partial_batch(env, unreadable):
    _write(env, "a-valid.csv", [_row(_PRODUCER, 2, "export"), _row(_CONSUMER, 1)])
    broken = _write(env, "b-broken.csv", [_row(_CONSUMER, 1, direction="not-a-direction")])
    if unreadable:
        broken.write_bytes(b"\xff\xfe\xff")  # Not UTF-8: the CSV parser raises instead of returning errors.
    originals = {p.name: p.read_bytes() for p in env.config.paths.inbox.iterdir()}
    before = _outputs(env)

    with pytest.raises(ValueError, match="b-broken.csv"):
        run(env.config, mqtt_client=env.mqtt, db_path=env.db)

    assert _outputs(env) == before
    assert {p.name: p.read_bytes() for p in env.config.paths.inbox.iterdir()} == originals
    env.mqtt.publish.assert_not_called()


def test_conflicting_files_without_a_database_cannot_publish_or_archive(env):
    _write(env, "a.csv", [_row(_CONSUMER, 1)])
    _write(env, "b.csv", [_row(_CONSUMER, 1.5)])
    before = _outputs(env)

    with pytest.raises(ValueError, match="Widersprüchliche Intervallmessung"):
        run(env.config, mqtt_client=env.mqtt)

    assert _outputs(env) == before
    assert len(list(env.config.paths.inbox.iterdir())) == 2
    env.mqtt.publish.assert_not_called()
