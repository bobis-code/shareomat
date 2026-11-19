# -*- coding: utf-8 -*-
"""SX-2 import + versioning and SX-3 official billing (ADR-0008), on a synthetic two-member LEG."""
from __future__ import annotations

import csv
import hashlib
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.core.collector.sdat_import import import_pending, import_sdat_file
from shareomat.database import billing
from shareomat.database.community import save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sdat_store import established_community_id, list_documents, load_official_values
from shareomat.database.settings import get_operation_settings, save_operation_settings
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.leg_const import METER_DATA_SOURCE_SDAT_LEG
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff
from shareomat.web.state import get_state
from tests.sdat_fixtures import (
    COMMUNITY,
    CONSUMER,
    OTHER_COMMUNITY,
    PRODUCER,
    RECEIVER_EIC,
    Document,
    Series,
    build,
    eic,
    leg_day,
)

DAY = date(2026, 12, 1)
SLOTS = 96
START = datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)


@pytest.fixture
def leg(tmp_path):
    db = tmp_path / "shareomat.db"
    init_db(db)
    save_community(db, Community(community_id="SYNTH-LEG", name="Synthetische Test-LEG", address_line="Beispielweg 0",
                                 postal_code="0000", city="Testort", eic_code=RECEIVER_EIC))
    for pid, meter_id, role in (("test-load", CONSUMER, "consumer"), ("test-pv", PRODUCER, "producer")):
        create_participant(db, Participant(pid, pid, address_line="Beispielweg 0", postal_code="0000", city="Testort"))
        create_meter(db, Meter(meter_id, pid, pid, role))
    create_tariff(db, Tariff(local_rate_chf_kwh=Decimal("0.10"), grid_rate_chf_kwh=Decimal("0"),
                             feed_in_rate_chf_kwh=Decimal("0.10"), valid_from=date(2026, 1, 1)))
    paths = {}
    for name in ("inbox", "archive", "reports", "state"):
        paths[name] = tmp_path / name
        paths[name].mkdir()
    runtime = RuntimeConfig(paths=PathConfig(**paths))
    get_state().register_db(db)
    get_state().register_runtime(runtime)
    return db, runtime, tmp_path


def _official_day(consumer_local="1.500", producer_local="1.500", conditions=None, **kwargs) -> Document:
    """Consumer draws 2 kWh, producer feeds 3 kWh per quarter hour; EBL shares 1.5 kWh (not min(2, 3) = 2)."""
    series = leg_day(["2.000"] * SLOTS, [consumer_local] * SLOTS, ["3.000"] * SLOTS, [producer_local] * SLOTS,
                     conditions=conditions)
    return Document(series=series, **kwargs)


def _file(tmp_path, name: str, data: bytes):
    path = tmp_path / name
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest(), name


def _import(db, tmp_path, name, doc_or_bytes):
    data = doc_or_bytes if isinstance(doc_or_bytes, bytes) else build(doc_or_bytes)
    path, sha, name = _file(tmp_path, name, data)
    return import_sdat_file(db, path, sha, file_name=name, origin="swisseldex")


def _use_sdat_source(db):
    save_operation_settings(db, replace(get_operation_settings(db), meter_data_source=METER_DATA_SOURCE_SDAT_LEG))


def _meter_readings(db) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM meter_readings").fetchone()[0]


# ── import ──


def test_import_establishes_the_community_and_keeps_meter_readings_untouched(leg):
    db, runtime, tmp = leg
    outcome = _import(db, tmp, "e66.xml", _official_day())
    assert outcome.imported and outcome.series == 6 and outcome.document_type == "E66"
    assert "Quittung" in outcome.message      # the sender asked for one; shown, not sent
    assert established_community_id(db) == COMMUNITY
    assert _meter_readings(db) == 0


def test_identical_delivery_is_not_imported_twice(leg):
    db, runtime, tmp = leg
    data = build(_official_day())
    assert _import(db, tmp, "a.xml", data).imported
    again = _import(db, tmp, "a-again.xml", data)
    assert again.skipped and len(list_documents(db)) == 1


def test_wrong_receiver_is_refused_and_nothing_stored(leg):
    db, runtime, tmp = leg
    outcome = _import(db, tmp, "x.xml", _official_day(receiver_eic=eic("12X-SOMEONEELSE")))
    assert not outcome.imported and outcome.reason_code == "WRONG_RECEIVER"
    assert load_official_values(db, START, START + timedelta(days=1)).values == {}


def test_wrong_role_or_business_reason_is_refused(leg):
    db, runtime, tmp = leg
    assert _import(db, tmp, "r.xml", _official_day(receiver_role="DEA")).reason_code == "WRONG_ROLE"
    assert _import(db, tmp, "b.xml", _official_day(business_reason="C38")).reason_code == "WRONG_BUSINESS_REASON"


def test_unknown_metering_point_refuses_the_file_until_it_is_added(leg):
    db, runtime, tmp = leg
    doc = _official_day()
    doc.series += [Series("CH1000000000000000000000000000003", "consumption", "total", ["1.000"] * SLOTS)]
    path, sha, name = _file(tmp, "e66.xml", build(doc))
    first = import_sdat_file(db, path, sha, file_name=name, origin="swisseldex")
    assert first.reason_code == "UNKNOWN_METERING_POINT" and "CH1000000000000000000000000000003" in first.message
    create_participant(db, Participant("test-new", "test-new", address_line="Beispielweg 0", postal_code="0000", city="Testort"))
    create_meter(db, Meter("CH1000000000000000000000000000003", "test-new", "test-new", "consumer"))
    assert import_pending(db, [(path, sha, name)], origin="swisseldex")[0].imported


def test_e31_waits_for_the_first_e66(leg):
    db, runtime, tmp = leg
    e31 = _file(tmp, "e31.xml", build(Document(document_type="E31", document_id="E31-1", series=[
        Series("12Y-TESTAREA000X", "consumption", "total", ["2.000"] * SLOTS)])))
    assert import_pending(db, [e31], origin="swisseldex")[0].reason_code == "COMMUNITY_UNKNOWN"
    e66 = _file(tmp, "e66.xml", build(_official_day()))
    results = import_pending(db, [e31, e66], origin="swisseldex")
    assert [r.document_type for r in results] == ["E66", "E31"] and all(r.imported for r in results)


def test_foreign_community_in_the_same_document_is_not_stored(leg):
    db, runtime, tmp = leg
    doc = _official_day()
    foreign = Series("CH1000000000000000000000000000009", "consumption", "total", ["5.000"] * SLOTS, community=OTHER_COMMUNITY)
    doc.series.append(foreign)
    outcome = _import(db, tmp, "two.xml", doc)
    assert outcome.imported and outcome.series == 6
    assert list_documents(db)[0].foreign_series_count == 1


def test_changed_community_id_is_refused(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "first.xml", _official_day())
    moved = Document(document_id="DOC-2", series=leg_day(["2.000"] * SLOTS, ["1.000"] * SLOTS, ["3.000"] * SLOTS,
                                                          ["1.000"] * SLOTS, community=OTHER_COMMUNITY))
    assert _import(db, tmp, "moved.xml", moved).reason_code == "WRONG_COMMUNITY"


# ── versions of the same quarter hours ──


def _local_consumer(db):
    values = load_official_values(db, START, START + timedelta(days=1)).values
    return values[(CONSUMER, "consumption", "local", START)]


def test_newer_delivery_replaces_older(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "v1.xml", _official_day(conditions=["21"] * SLOTS, document_id="V1"))
    _import(db, tmp, "v2.xml", _official_day(consumer_local="1.250", producer_local="1.250",
                                             created=datetime(2026, 12, 8, tzinfo=timezone.utc), document_id="V2"))
    current = _local_consumer(db)
    assert current.value == Decimal("1.250") and current.condition == ""


def test_temporary_value_never_replaces_a_plausibilised_one(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "final.xml", _official_day(document_id="FINAL"))
    _import(db, tmp, "late-temp.xml", _official_day(consumer_local="0.100", producer_local="0.100", conditions=["21"] * SLOTS,
                                                    created=datetime(2026, 12, 9, tzinfo=timezone.utc), document_id="LATE"))
    data = load_official_values(db, START, START + timedelta(days=1))
    assert data.values[(CONSUMER, "consumption", "local", START)].value == Decimal("1.500")
    assert data.downgrades == 6 * SLOTS


def test_cancellation_removes_the_values(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "orig.xml", _official_day(document_id="ORIG"))
    _import(db, tmp, "cancel.xml", _official_day(status="1", created=datetime(2026, 12, 9, tzinfo=timezone.utc), document_id="CANCEL"))
    assert load_official_values(db, START, START + timedelta(days=1)).values == {}


# ── official billing (SX-3) ──


def test_official_split_is_billed_as_delivered_not_redistributed(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    _use_sdat_source(db)
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    lines = {i.participant_id: i for i in preview.line_items}
    # match_all would share min(2, 3) = 2 kWh per quarter hour (192 kWh); EBL allocated 1.5 kWh (144 kWh).
    assert lines["test-load"].local_received_kwh == Decimal("144.0000")
    assert lines["test-load"].grid_import_kwh == Decimal("48.0000")
    assert lines["test-pv"].local_supplied_kwh == Decimal("144.0000")
    assert lines["test-pv"].grid_export_kwh == Decimal("144.0000")
    assert lines["test-load"].local_amount_chf == Decimal("14.4000")
    assert preview.availability.hard_issues == []
    assert [s.origin for s in preview.availability.sources] == ["sdat"]


def test_temporary_values_block_the_release(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "e66.xml", _official_day(conditions=["21"] * SLOTS))
    _use_sdat_source(db)
    hard = billing.compute_billing_preview(db, runtime, DAY, DAY).availability.hard_issues
    assert any("vorläufige" in issue for issue in hard)


def test_missing_residual_series_is_never_assumed_zero(leg):
    db, runtime, tmp = leg
    doc = _official_day()
    doc.series = [s for s in doc.series if not (s.point == CONSUMER and s.energy_type == "residual")]
    _import(db, tmp, "e66.xml", doc)
    _use_sdat_source(db)
    hard = billing.compute_billing_preview(db, runtime, DAY, DAY).availability.hard_issues
    assert any(CONSUMER in issue and "fehlt LEG- oder Restenergie" in issue for issue in hard)


def test_total_not_equal_local_plus_residual_blocks(leg):
    db, runtime, tmp = leg
    doc = _official_day()
    for s in doc.series:
        if s.point == CONSUMER and s.energy_type == "residual":
            s.values = ["0.400"] * SLOTS
    _import(db, tmp, "e66.xml", doc)
    _use_sdat_source(db)
    hard = billing.compute_billing_preview(db, runtime, DAY, DAY).availability.hard_issues
    assert any("Total ≠ LEG-Energie + Restenergie" in issue for issue in hard)


def test_official_energy_of_a_meter_not_billed_that_day_blocks(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    _use_sdat_source(db)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meters SET valid_from = '2026-12-05' WHERE meter_id = ?", (PRODUCER,))
    hard = billing.compute_billing_preview(db, runtime, DAY, DAY).availability.hard_issues
    assert any(PRODUCER in issue and "nicht abrechnungsberechtigt" in issue for issue in hard)


def test_no_imported_data_is_a_hard_issue(leg):
    db, runtime, tmp = leg
    _use_sdat_source(db)
    hard = billing.compute_billing_preview(db, runtime, DAY, DAY).availability.hard_issues
    assert any("noch keine offiziellen E66-Daten" in issue for issue in hard)


def test_e31_crosscheck_is_only_a_hint(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    _use_sdat_source(db)
    issues = billing.compute_billing_preview(db, runtime, DAY, DAY).availability.blocking_issues
    assert any("Keine E31-Summen" in issue for issue in issues)
    e31 = Document(document_type="E31", document_id="E31", series=[
        Series("12Y-TESTAREA000X", "consumption", "local", ["1.400"] * SLOTS)])
    assert _import(db, tmp, "e31.xml", e31).imported
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    assert any("E31-Kontrolle Verbrauch/LEG-Energie" in issue for issue in preview.availability.blocking_issues)
    assert not any("E31" in issue for issue in preview.availability.hard_issues)


def test_release_and_later_correction_never_changes_the_released_run(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    _use_sdat_source(db)
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    draft = billing.save_draft(db, preview)
    released = billing.release_billing_run(db, draft.id, override_reason="Test: E31 fehlt", runtime=runtime)
    assert released.status == "released"
    window = (START, START + timedelta(days=1))
    assert billing.released_runs_needing_review(db, runtime, *window) == []

    _import(db, tmp, "corr.xml", _official_day(consumer_local="1.000", producer_local="1.000", status="5",
                                               created=datetime(2026, 12, 10, tzinfo=timezone.utc), document_id="CORR"))
    affected = billing.released_runs_needing_review(db, runtime, *window)
    assert [r.id for r in affected] == [draft.id]
    detail = billing.get_billing_run(db, draft.id)
    assert detail.run.status == "released"
    assert {i.participant_id: i.local_received_kwh for i in detail.line_items}["test-load"] == Decimal("144.0000")


def test_new_sdat_data_between_draft_and_release_refuses_the_release(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    _use_sdat_source(db)
    draft = billing.save_draft(db, billing.compute_billing_preview(db, runtime, DAY, DAY))
    _import(db, tmp, "corr.xml", _official_day(consumer_local="1.000", producer_local="1.000",
                                               created=datetime(2026, 12, 10, tzinfo=timezone.utc), document_id="CORR"))
    with pytest.raises(billing.BillingWorkflowError, match="geändert"):
        billing.release_billing_run(db, draft.id, override_reason="x", runtime=runtime)


def test_default_source_ignores_sdat_data_completely(leg):
    """Bestandsschutz: with the existing data source, imported SDAT data changes nothing."""
    db, runtime, tmp = leg
    source = runtime.paths.inbox / "test-day.csv"
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        for slot in range(SLOTS):
            ts = (START + timedelta(minutes=15 * slot)).isoformat()
            writer.writerow([ts, PRODUCER, "3", "export"])
            writer.writerow([ts, CONSUMER, "2", "import"])
    before = billing.compute_billing_preview(db, runtime, DAY, DAY, persist_readings=False)
    _import(db, tmp, "e66.xml", _official_day())
    after = billing.compute_billing_preview(db, runtime, DAY, DAY, persist_readings=False)
    assert billing._result_fingerprint(before) == billing._result_fingerprint(after)
    assert {i.participant_id: i.local_received_kwh for i in after.line_items}["test-load"] == Decimal("192.0000")


# ── rounding of the grid operator (first real EBL deliveries, 11.2026) ──


def _with_consumer(doc, energy_type, slot_values: dict[int, str]):
    for s in doc.series:
        if s.point == CONSUMER and s.energy_type == energy_type:
            values = list(s.values)
            for index, value in slot_values.items():
                values[index] = value
            s.values = values
    return doc


def test_total_off_by_one_digit_is_rounding_not_a_contradiction(leg):
    db, runtime, tmp = leg
    # 2.000 total vs 1.500 + 0.501: EBL rounds each value on its own.
    _import(db, tmp, "e66.xml", _with_consumer(_official_day(), "residual", {3: "0.501", 40: "0.499", 77: "0.501"}))
    _use_sdat_source(db)
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    assert not any("Total ≠" in issue for issue in preview.availability.blocking_issues)
    assert any(CONSUMER in note and "3 Viertelstunde(n)" in note and "Rundung" in note
               for note in preview.availability.notes)
    # The LEG energy is billed as delivered, the rounding changes nothing.
    consumer = next(i for i in preview.line_items if i.participant_id == "test-load")
    assert consumer.local_received_kwh == Decimal("144.0000")


def test_total_off_by_two_digits_still_blocks(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "e66.xml", _with_consumer(_official_day(), "residual", {3: "0.502"}))
    _use_sdat_source(db)
    hard = billing.compute_billing_preview(db, runtime, DAY, DAY).availability.hard_issues
    assert any("Total ≠ LEG-Energie + Restenergie" in issue for issue in hard)


def test_clearing_rounding_per_quarter_hour_is_only_a_note(leg):
    db, runtime, tmp = leg
    doc = _with_consumer(_official_day(), "local", {5: "1.501", 6: "1.501"})
    _with_consumer(doc, "residual", {5: "0.499", 6: "0.499"})
    _import(db, tmp, "e66.xml", doc)
    _use_sdat_source(db)
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    assert not any("Energie-Clearing" in issue for issue in preview.availability.blocking_issues)
    assert any("Energie-Clearing: in 2 Viertelstunde(n) Rundungsdifferenz" in note and "+0.002 kWh" in note
               for note in preview.availability.notes)


def test_clearing_difference_beyond_rounding_blocks(leg):
    db, runtime, tmp = leg
    doc = _with_consumer(_official_day(), "local", {5: "1.502"})
    _with_consumer(doc, "residual", {5: "0.498"})
    _import(db, tmp, "e66.xml", doc)
    _use_sdat_source(db)
    hard = billing.compute_billing_preview(db, runtime, DAY, DAY).availability.hard_issues
    assert any("Energie-Clearing stimmt nicht: in 1 Viertelstunde(n)" in issue for issue in hard)
