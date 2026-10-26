# -*- coding: utf-8 -*-
"""Regression tests for the billing quality findings of the 26.10.2026 review (S3, S5, S6, S8, S12, S13, S14)."""

from __future__ import annotations

import csv
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.core.pipeline.leg_normalizer import normalize_ebl_row
from shareomat.core.pipeline.leg_parser import parse_csv
from shareomat.core.pipeline.raw.ebl_xlsx import EblRow, _build_ebl_rows
from shareomat.database.audit import list_events
from shareomat.database.billing import (
    BillingWorkflowError,
    _period_bounds,
    _slots_in_local_day,
    compute_billing_preview,
    get_billing_run,
    release_billing_run,
    save_draft,
)
from shareomat.database.community import save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff

ZURICH = ZoneInfo("Europe/Zurich")


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


def _line(preview, pid):
    return next(i for i in preview.line_items if i.participant_id == pid)


# ── S5 local month bounds, DST quarter hours ────────────────────────────────


def test_month_starts_at_swiss_midnight():
    start, end = _period_bounds(date(2026, 11, 1), date(2026, 11, 30))
    assert start == datetime(2026, 10, 31, 23, 0, tzinfo=timezone.utc)
    assert end == datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)
    start_summer, _ = _period_bounds(date(2027, 7, 1), date(2027, 7, 31))
    assert start_summer == datetime(2027, 6, 30, 22, 0, tzinfo=timezone.utc)


def test_dst_days_have_92_and_100_quarter_hours():
    assert _slots_in_local_day(date(2027, 3, 28)) == 92
    assert _slots_in_local_day(date(2026, 10, 25)) == 100
    assert _slots_in_local_day(date(2026, 11, 1)) == 96


def test_first_local_hour_of_november_is_billed_in_november(db_path, runtime):
    # 01.11.2026 00:30 local = 31.10.2026 23:30 UTC
    _write_csv(runtime.paths.inbox, "nov.csv", [
        ("2026-10-31T23:30:00+00:00", "M1", "1.0", "export"),
        ("2026-10-31T23:30:00+00:00", "M2", "1.0", "import"),
    ])
    november = compute_billing_preview(db_path, runtime, date(2026, 11, 1), date(2026, 11, 30))
    october = compute_billing_preview(db_path, runtime, date(2026, 10, 1), date(2026, 10, 31))
    assert _line(november, "cons_a").local_received_kwh == Decimal("1.0")
    assert not october.line_items


# ── S6 duplicates ───────────────────────────────────────────────────────────


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


# ── S3 / S12 producer credit, rounding, prosumer netting ────────────────────


def test_producer_credit_is_stored_and_prosumer_is_netted(db_path, runtime):
    _write_csv(runtime.paths.inbox, "jul.csv", [
        ("2027-07-01T10:00:00+00:00", "M1", "1.0", "export"),
        ("2027-07-01T10:00:00+00:00", "M3", "0.333", "export"),
        ("2027-07-01T10:00:00+00:00", "M2", "1.0", "import"),
        ("2027-07-01T11:00:00+00:00", "M3", "0.5", "import"),
        ("2027-07-01T11:00:00+00:00", "M1", "0.5", "export"),
    ])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    solar, cons, pros = _line(preview, "solar"), _line(preview, "cons_a"), _line(preview, "prosumer")
    # Σ consumer charges == Σ producer credits (same price, no fee), before rounding.
    assert sum(i.local_amount_chf for i in preview.line_items) == sum(i.producer_credit_chf for i in preview.line_items)
    assert solar.producer_credit_chf > 0 and solar.charge_chf == Decimal("0.00")
    # Prosumer: separate charge and credit, net = charge - credit, all rounded to 0.01.
    assert pros.charge_chf == pros.local_amount_chf.quantize(Decimal("0.01"))
    assert pros.credit_chf == pros.producer_credit_chf.quantize(Decimal("0.01"))
    assert pros.net_amount_chf == pros.charge_chf - pros.credit_chf
    assert cons.net_amount_chf == cons.charge_chf

    run = save_draft(db_path, preview)
    stored = {i.participant_id: i for i in get_billing_run(db_path, run.id).line_items}
    assert stored["solar"].producer_credit_chf == solar.producer_credit_chf
    assert stored["prosumer"].net_amount_chf == pros.net_amount_chf
    assert get_billing_run(db_path, run.id).run.total_producer_credit_chf == preview.total_producer_credit_chf


# ── S8 quality gate ─────────────────────────────────────────────────────────


def test_incomplete_data_blocks_release_unless_a_reason_is_given(db_path, runtime):
    _write_csv(runtime.paths.inbox, "jul.csv", [("2027-07-01T10:00:00+00:00", "M1", "1.0", "export"),
                                                 ("2027-07-01T10:00:00+00:00", "M2", "1.0", "import")])
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    assert any("Viertelstunden ohne Messwert" in i for i in preview.availability.blocking_issues)
    run = save_draft(db_path, preview)
    with pytest.raises(BillingWorkflowError, match="Freigabe gesperrt"):
        release_billing_run(db_path, run.id)
    released = release_billing_run(db_path, run.id, override_reason="Probelauf, Daten unvollständig")
    assert released.release_override_reason == "Probelauf, Daten unvollständig"
    assert "Begründung: Probelauf" in list_events(db_path, entity="billing_run", entity_id=run.id)[0].detail


def test_complete_month_passes_the_gate(db_path, runtime):
    rows = []
    t = datetime(2027, 1, 31, 23, 0, tzinfo=timezone.utc)   # 01.02.2027 00:00 local
    end = datetime(2027, 2, 28, 23, 0, tzinfo=timezone.utc)
    while t < end:
        stamp = t.isoformat()
        rows += [(stamp, "M1", "0.5", "export"), (stamp, "M2", "0.3", "import"),
                 (stamp, "M3", "0", "import"), (stamp, "M3", "0", "export")]
        t += timedelta(minutes=15)
    _write_csv(runtime.paths.inbox, "feb.csv", rows)
    preview = compute_billing_preview(db_path, runtime, date(2027, 2, 1), date(2027, 2, 28))
    assert preview.availability.blocking_issues == []
    run = save_draft(db_path, preview)
    assert release_billing_run(db_path, run.id).status == "released"


# ── S13 / S14 input validation ──────────────────────────────────────────────


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


# ── S11 reports are never overwritten ───────────────────────────────────────


def test_second_report_for_same_period_gets_a_new_file(tmp_path):
    from shareomat.core.report.leg_report import _new_report_path
    start = datetime(2027, 7, 1, tzinfo=timezone.utc)
    first = _new_report_path(tmp_path, "billing", start, ".csv")
    first.write_text("v1", encoding="utf-8")
    second = _new_report_path(tmp_path, "billing", start, ".csv")
    assert second != first and second.name.endswith("_v2.csv")
    assert first.read_text(encoding="utf-8") == "v1"



# ── S17 billed meters keep their participant ────────────────────────────────


def test_billed_meter_cannot_be_moved_to_another_participant(db_path, runtime):
    from shareomat.database.meters import update_meter
    _write_csv(runtime.paths.inbox, "jul.csv", [("2027-07-01T10:00:00+00:00", "M1", "1.0", "export"),
                                                 ("2027-07-01T10:00:00+00:00", "M2", "1.0", "import")])
    run = save_draft(db_path, compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31)))
    update_meter(db_path, "M2", Meter("M2", "prosumer", "Bezug", "consumer"))   # draft only: still allowed
    update_meter(db_path, "M2", Meter("M2", "cons_a", "Bezug", "consumer"))
    release_billing_run(db_path, run.id, override_reason="Test")
    with pytest.raises(ValueError, match="freigegebenen Abrechnung"):
        update_meter(db_path, "M2", Meter("M2", "prosumer", "Bezug", "consumer"))
    update_meter(db_path, "M2", Meter("M2", "cons_a", "Bezug neu", "consumer"))  # other edits stay possible
