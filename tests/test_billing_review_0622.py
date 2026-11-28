# -*- coding: utf-8 -*-
"""Review 20.11.2026 (0.6.22): money rounded once from exact amounts, official billing only from the
monthly delivery and complete, rounding notes kept with the run, a prosumer with two metering points."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from shareomat.database import billing
from shareomat.database.tariffs import create_tariff
from shareomat.models.tariff import Tariff
from tests.sdat_fixtures import CONSUMER, PRODUCER, Document, leg_day
from tests.test_sdat_import_and_billing import (  # noqa: F401  (leg is a fixture)
    DAY,
    SLOTS,
    _import,
    _official_day,
    _with_consumer,
    leg,
)

DAY_START = datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)


def _preview(db, runtime):
    return billing.compute_billing_preview(db, runtime, DAY, DAY)


def _issues(preview, text):
    return [i for i in preview.availability.blocking_issues if text in i]


# ── money: one rounding only ──


def test_review_example_is_not_rounded_twice(leg):
    """0.045 kWh x 0.111 CHF = 0.004995 CHF: 0.00 on the invoice, not 0.0050 -> 0.01."""
    db, runtime, tmp = leg
    create_tariff(db, Tariff(local_rate_chf_kwh=Decimal("0.111"), grid_rate_chf_kwh=Decimal("0"),
                             feed_in_rate_chf_kwh=Decimal("0.111"), valid_from=DAY, name="Review"))
    local = ["0.045"] + ["0.000"] * (SLOTS - 1)
    series = leg_day(["2.000"] * SLOTS, local, ["3.000"] * SLOTS, local)
    _import(db, tmp, "e66.xml", Document(series=series, report_start=DAY_START,
                                         report_end=datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)))
    lines = {i.participant_id: i for i in _preview(db, runtime).line_items}
    consumer, producer = lines["test-load"], lines["test-pv"]
    assert consumer.local_amount_exact_chf == Decimal("0.004995")
    assert str(consumer.local_amount_chf) == "0.005"               # shown to 4 places, never rounded again
    assert consumer.charge_chf == Decimal("0.00") and producer.credit_chf == Decimal("0.00")


def test_amounts_are_exact_decimal_products(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "e66.xml", _official_day(consumer_local="0.333", producer_local="0.333"))
    line = {i.participant_id: i for i in _preview(db, runtime).line_items}["test-load"]
    # 96 x 0.333 kWh x 0.10 CHF exactly; float would carry binary residue.
    assert line.local_amount_exact_chf == Decimal("0.333") * 96 * Decimal("0.10")
    assert line.charge_chf == Decimal("3.20")


# ── official data: monthly delivery and completeness ──


def test_daily_deliveries_alone_never_allow_a_release(leg):
    db, runtime, tmp = leg
    daily = _official_day(report_start=DAY_START, report_end=DAY_START + timedelta(days=1))
    _import(db, tmp, "daily.xml", daily)
    preview = _preview(db, runtime)
    assert any("noch keine Monatslieferung" in i for i in preview.availability.hard_issues)


def test_value_only_in_a_daily_delivery_blocks(leg):
    db, runtime, tmp = leg
    monthly = _official_day(document_id="M1")
    monthly.series = [s for s in monthly.series if s.point == CONSUMER]          # producer missing in the month
    _import(db, tmp, "month.xml", monthly)
    _import(db, tmp, "daily.xml", _official_day(document_id="D1", report_start=DAY_START,
                                                report_end=DAY_START + timedelta(days=1),
                                                created=datetime(2026, 12, 8, tzinfo=timezone.utc)))
    hard = _preview(db, runtime).availability.hard_issues
    assert any(PRODUCER in i and "nur aus Tageslieferungen" in i for i in hard)
    assert not any(CONSUMER in i and "nur aus Tageslieferungen" in i for i in hard)


def test_later_daily_value_differing_from_the_month_needs_a_reason(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "month.xml", _official_day(document_id="M1"))
    later = _with_consumer(_official_day(document_id="D9", report_start=DAY_START,
                                         report_end=DAY_START + timedelta(days=1),
                                         created=datetime(2026, 12, 9, tzinfo=timezone.utc)), "local", {3: "1.400"})
    _with_consumer(later, "residual", {3: "0.600"})
    _import(db, tmp, "daily.xml", later)
    preview = _preview(db, runtime)
    soft = [i for i in preview.availability.blocking_issues if i not in preview.availability.hard_issues]
    assert any("weicht ab" in i or "abweicht" in i for i in soft)
    assert not any("Monatslieferung" in i for i in preview.availability.hard_issues)


def test_missing_quarter_hours_are_left_out_and_need_a_reason(leg):
    """Until 0.6.33 a hard issue (review 20.11.2026); since the operator's decision of 28.11.2026 the
    quarter hours without a value are not billed, for the whole LEG, and release needs a reason."""
    db, runtime, tmp = leg
    doc = _official_day()
    for s in doc.series:
        if s.point == PRODUCER:
            s.values = s.values[:50]
    _import(db, tmp, "month.xml", doc)
    availability = _preview(db, runtime).availability
    soft = [i for i in availability.blocking_issues if i not in availability.hard_issues]
    assert any("46 von 96 Viertelstunden ohne Messwert" in i for i in soft)
    assert any("Nicht abgerechnet: 46 Viertelstunde(n)" in i for i in soft)
    assert not any("ohne Messwert" in i for i in availability.hard_issues)


# ── notes kept with the run ──


def test_rounding_notes_are_stored_with_the_run(leg):
    db, runtime, tmp = leg
    doc = _with_consumer(_official_day(), "local", {5: "1.501"})       # received 0.001 more than supplied
    _with_consumer(doc, "residual", {5: "0.499"})
    _import(db, tmp, "month.xml", doc)
    preview = _preview(db, runtime)
    assert any("Rundungsdifferenz" in n for n in preview.availability.notes)
    run = billing.save_draft(db, preview)
    stored = billing.get_billing_run(db, run.id).run
    assert stored.notes == preview.availability.notes


# ── prosumer with two metering point ids ──


def test_prosumer_with_two_metering_points_gets_both_lines(leg):
    """SDAT-CH allows a participant's flows under two metering point ids: both are one participant."""
    db, runtime, tmp = leg
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meters SET participant_id = (SELECT participant_id FROM meters WHERE meter_id = ?) "
                     "WHERE meter_id = ?", (CONSUMER, PRODUCER))
    _import(db, tmp, "month.xml", _official_day())
    preview = _preview(db, runtime)
    [line] = preview.line_items
    assert sorted(line.meter_ids) == sorted([CONSUMER, PRODUCER])
    assert line.local_received_kwh == Decimal("144.0000") and line.local_supplied_kwh == Decimal("144.0000")
    assert line.net_amount_chf == line.charge_chf - line.credit_chf


# ── confirmed LEG start: allocated energy before it is never dropped silently ──


def test_official_leg_energy_before_the_confirmed_start_blocks(leg, monkeypatch):
    """Entering a later start than the grid operator really allocated from must not make energy free."""
    from dataclasses import replace

    import shareomat.database.contract_versions as contract_versions_module
    from shareomat.database.community import get_community, save_community
    from shareomat.database.contract_versions import publish_version, save_draft_version
    from shareomat.database.participant_contract import assign_contract, mark_accepted
    from shareomat.models.contract import ContractVersion

    db, runtime, tmp = leg

    class _Today(date):
        @classmethod
        def today(cls):
            return date(2025, 1, 1)

    monkeypatch.setattr(contract_versions_module, "date", _Today)
    version = publish_version(db, save_draft_version(db, ContractVersion(
        community_id="", version=0, status="draft", contract_text_snapshot="<p>Vertrag</p>",
        local_rate_chf_kwh=Decimal("0.10"), feed_in_rate_chf_kwh=Decimal("0.10"), grid_operator="Test-VNB",
        representative_name="Test Vertretung", representative_address_line="Beispielweg 0",
        representative_postal_code="0000", representative_city="Testort",
        representative_email="vertretung@example.ch", valid_from=date(2026, 1, 1))).id)
    monkeypatch.setattr(contract_versions_module, "date", date)
    for pid in ("test-load", "test-pv"):
        assignment = assign_contract(db, pid, version.id, version.tariff_id, joined_at=date(2026, 1, 1))
        mark_accepted(db, assignment.id, date(2025, 12, 1), method="paper", signed_by="Test",
                      paper_location="Ordner 1")
    _import(db, tmp, "month.xml", _official_day())
    assert not any("nicht abrechnungsberechtigt" in i for i in _preview(db, runtime).availability.hard_issues)

    save_community(db, replace(get_community(db), vnb_confirmed_start=DAY + timedelta(days=1)))
    hard = _preview(db, runtime).availability.hard_issues
    for point in (CONSUMER, PRODUCER):
        assert any(point in i and "nicht abrechnungsberechtigt" in i for i in hard), hard
