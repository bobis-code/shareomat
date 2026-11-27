# -*- coding: utf-8 -*-
"""CSV and EBL-Excel parts of the billing quality review of 26.10.2026 (S6, S11, S13, S14, S-11, S-19).

These tests check the CSV path itself: parsers, report files, duplicate and
conflicting CSV files. The path is removed with P2c (ADR-0012), and this file
goes with it. The billing rules themselves are tested on official SDAT data in
tests/test_billing_quality.py.
"""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.core.pipeline.leg_normalizer import normalize_ebl_row
from shareomat.core.pipeline.leg_parser import parse_csv
from shareomat.core.pipeline.raw.ebl_xlsx import EblRow, _build_ebl_rows
from shareomat.database.billing import compute_billing_preview
from shareomat.database.community import save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    save_community(path, Community(community_id="ZEV-001", name="Test"))
    create_participant(path, Participant("solar", "Solar PV"))
    create_participant(path, Participant("cons_a", "Consumer A"))
    create_participant(path, Participant("prosumer", "Prosumer"))
    create_meter(path, Meter("M1", "solar", "PV", "producer"))
    create_meter(path, Meter("M2", "cons_a", "Bezug", "consumer"))
    create_meter(path, Meter("M3", "prosumer", "Haus", "producer_consumer"))
    create_tariff(path, Tariff(
        local_rate_chf_kwh=Decimal("0.11"), grid_rate_chf_kwh=Decimal("0"),
        feed_in_rate_chf_kwh=Decimal("0.11"), valid_from=date(2020, 1, 1),
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


def _write_csv(directory: Path, filename: str, rows: list[tuple]) -> Path:
    path = directory / filename
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        writer.writerows(rows)
    return path


_ALL_CHANNELS_ONE_SLOT = [
    ("2027-07-01T10:00:00+00:00", "M1", "1.0", "export"),
    ("2027-07-01T10:00:00+00:00", "M2", "1.0", "import"),
    ("2027-07-01T10:00:00+00:00", "M3", "0", "import"),
    ("2027-07-01T10:00:00+00:00", "M3", "0", "export"),
]


def _line(preview, pid):
    return next(i for i in preview.line_items if i.participant_id == pid)


def test_same_file_in_inbox_and_archive_counts_once(db_path, runtime):
    rows = [("2027-07-01T10:00:00+00:00", "M1", "1.0", "export"),
            ("2027-07-01T10:00:00+00:00", "M2", "1.0", "import")]
    _write_csv(runtime.paths.inbox, "jul.csv", rows)
    _write_csv(runtime.paths.archive, "jul.csv", rows)
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    assert _line(preview, "cons_a").local_received_kwh == Decimal("1.0")   # not 2.0
    assert len(preview.availability.sources) == 1


def test_conflicting_values_are_not_summed_and_block_release(db_path, runtime):
    old = _write_csv(runtime.paths.inbox, "a.csv", [("2027-07-01T10:00:00+00:00", "M2", "1.0", "import"),
                                                    ("2027-07-01T10:00:00+00:00", "M1", "2.0", "export")])
    os.utime(old, (1_700_000_000, 1_700_000_000))
    _write_csv(runtime.paths.inbox, "b-korrektur.csv", [("2027-07-01T10:00:00+00:00", "M2", "1.5", "import")])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    assert _line(preview, "cons_a").local_received_kwh == Decimal("1.5")   # newer file wins, never 2.5
    assert any("Widersprüchliche Werte" in i for i in preview.availability.blocking_issues)


@pytest.mark.parametrize("bad", ["nan", "inf", "-1"])
def test_csv_rejects_non_finite_or_negative_values(tmp_path, bad):
    path = _write_csv(tmp_path, "bad.csv", [("2027-07-01T10:00:00+00:00", "M1", bad, "export"),
                                            ("2027-07-01T10:15:00+00:00", "M1", "1.0", "export")])
    readings = parse_csv(path, 15)
    assert [r.value_kwh for r in readings] == [1.0]   # the bad row never becomes a reading


def test_ebl_repeated_autumn_hour_maps_to_distinct_quarter_hours():
    # 25.10.2026: 02:15 local appears twice (CEST, then CET) — end-of-interval timestamps.
    from shareomat.core.pipeline.raw import ebl_xlsx
    data = [None] * ebl_xlsx._HEADER_ROWS
    for ts in (datetime(2026, 10, 25, 2, 15), datetime(2026, 10, 25, 2, 15)):
        row = [None] * (max(ebl_xlsx._COL_TIMESTAMP, ebl_xlsx._COL_BEZUG, ebl_xlsx._COL_RUECK) + 1)
        row[ebl_xlsx._COL_TIMESTAMP] = ts
        row[ebl_xlsx._COL_BEZUG] = 1.0
        row[ebl_xlsx._COL_RUECK] = 0.0
        data.append(row)
    parsed = _build_ebl_rows(data, "M2", "okt.xlsx")
    assert [r.fold for r in parsed] == [0, 1]
    slots = [normalize_ebl_row(r)[0].slot_start for r in parsed]
    assert slots[0] != slots[1] and slots[1] - slots[0] == timedelta(hours=1)


def test_ebl_zero_values_are_kept_as_measurements():
    readings = normalize_ebl_row(EblRow(meter_id="M1", timestamp_end=datetime(2027, 1, 10, 3, 0)))
    assert {r.direction for r in readings} == {"import", "export"}
    assert all(r.value_kwh == 0 for r in readings)


def test_second_report_for_same_period_gets_a_new_file(tmp_path):
    from shareomat.core.report.leg_report import _new_report_path
    start = datetime(2027, 7, 1, tzinfo=timezone.utc)
    first = _new_report_path(tmp_path, "billing", start, ".csv")
    first.write_text("v1", encoding="utf-8")
    second = _new_report_path(tmp_path, "billing", start, ".csv")
    assert second != first and second.name.endswith("_v2.csv")
    assert first.read_text(encoding="utf-8") == "v1"


def test_identical_duplicate_rows_in_one_csv_count_once(tmp_path):
    """S-11 reproduced: the automatic path summed duplicate rows within one file."""
    path = _write_csv(tmp_path, "dup.csv", [("2027-07-01T10:00:00+00:00", "M1", "1.0", "export"),
                                            ("2027-07-01T10:00:00+00:00", "M1", "1.0", "export")])
    assert [r.value_kwh for r in parse_csv(path, 15)] == [1.0]
    errors: list[str] = []
    path2 = _write_csv(tmp_path, "conflict.csv", [("2027-07-01T10:00:00+00:00", "M1", "1.0", "export"),
                                                  ("2027-07-01T10:00:00+00:00", "M1", "2.0", "export")])
    assert [r.value_kwh for r in parse_csv(path2, 15, errors=errors)] == [1.0]
    assert errors and "widersprüchliche Doppelzeile" in errors[0]


def test_rejected_rows_block_release(db_path, runtime):
    """S-19: rows the parser skips are reported, not silently dropped."""
    _write_csv(runtime.paths.inbox, "jul.csv", _ALL_CHANNELS_ONE_SLOT + [("2027-07-01T11:00:00+00:00", "M1", "abc", "export")])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    assert any("Zeile(n) abgewiesen" in i for i in preview.availability.hard_issues)
