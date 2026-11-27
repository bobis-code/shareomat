# -*- coding: utf-8 -*-
"""CSV-only part of the billing workflow tests: an unknown meter in a CSV file.

With official SDAT data an unknown metering point never gets in: the importer
refuses the whole delivery (tests/test_sdat_import_and_billing.py). The CSV
path is removed with P2c (ADR-0012), and this file goes with it.
"""

from __future__ import annotations

import csv
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.billing import (
    compute_billing_preview,
)
from shareomat.database.community import save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.leg_const import (
    METER_ROLE_CONSUMER,
    METER_ROLE_PRODUCER,
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
    create_participant(db_path, Participant("solar", "Solar PV"))
    create_participant(db_path, Participant("cons_a", "Consumer A"))
    create_meter(db_path, Meter("M1", "solar", "PV Meter", METER_ROLE_PRODUCER))
    create_meter(db_path, Meter("M2", "cons_a", "Meter A", METER_ROLE_CONSUMER))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal(tariff_rate), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=valid_from,
    ))


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
