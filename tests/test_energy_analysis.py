# -*- coding: utf-8 -*-
"""Analyse → "Summen & Auswertung" and "Messwerte" on the official E66 data (synthetic LEG, read-only)."""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sdat_analysis import period_sums, point_values, quarter_hour_origin
from shareomat.database.sdat_store import load_official_values
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.web.navigation import NAV_GROUPS
from shareomat.web.pages import analysis, meter_data
from shareomat.web.rendering import RequestContext
from shareomat.web.pages import energy_analysis
from tests.sdat_fixtures import CONSUMER, PRODUCER
from tests.test_sdat_import_and_billing import (  # noqa: F401  (leg is a fixture)
    DAY,
    SLOTS,
    START,
    _import,
    _official_day,
    _with_consumer,
    leg,
)

NEXT_DAY = DAY + timedelta(days=1)


@pytest.fixture(autouse=True)
def _today(monkeypatch):
    """The synthetic data is in December 2026: the pages see 02.12.2026 as today."""
    monkeypatch.setattr(energy_analysis, "_today", lambda: NEXT_DAY)


def _get(*segments, **query) -> RequestContext:
    return RequestContext(method="GET", ingress_path="", segments=list(segments), query=query)


def _points(sums):
    return {p.metering_point_id: p for p in sums.points}


# ── sums ──


def test_month_sums_per_point_match_the_values_billing_uses(leg):
    db, _, tmp = leg
    assert not period_sums(db, date(2026, 12, 1), date(2027, 1, 1)).points[0].check.complete
    _import(db, tmp, "e66.xml", _official_day())
    sums = period_sums(db, date(2026, 12, 1), date(2027, 1, 1))
    consumer, producer = _points(sums)[CONSUMER], _points(sums)[PRODUCER]
    assert (consumer.flows.consumption_total, consumer.flows.consumption_local,
            consumer.flows.consumption_residual) == (Decimal("192.000"), Decimal("144.000"), Decimal("48.000"))
    assert (producer.flows.production_total, producer.flows.production_local,
            producer.flows.production_residual) == (Decimal("288.000"), Decimal("144.000"), Decimal("144.000"))
    assert consumer.known and consumer.participant == "test-load"
    # Completeness is counted up to the latest delivery (01.12.), not to the end of the month.
    assert (consumer.check.expected, consumer.check.complete, consumer.check.mismatched) == (96, 96, 0)
    assert sums.data_until == DAY and sums.files == 1 and sums.row_unit == "day" and len(sums.rows) == 31
    assert sums.rows[0].flows.consumption_total == Decimal("192.000") and sums.rows[0].check.expected == 192
    assert sums.rows[1].check.expected == 0                                   # not delivered yet: not "missing"

    official = load_official_values(db, START, START + timedelta(days=31))
    local_total = sum(v.value for (_, d, t, _), v in official.values.items() if d == "consumption" and t == "local")
    assert sums.total.consumption_local == local_total


def test_one_point_and_the_metering_point_filter_of_the_loader(leg):
    db, _, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    sums = period_sums(db, DAY, NEXT_DAY, metering_point_id=CONSUMER.lower())
    assert [p.metering_point_id for p in sums.points] == [CONSUMER]
    only = load_official_values(db, START, START + timedelta(days=1), metering_point_ids=[CONSUMER])
    every = load_official_values(db, START, START + timedelta(days=1))
    assert only.values == {k: v for k, v in every.values.items() if k[0] == CONSUMER}
    assert load_official_values(db, START, START + timedelta(days=1), metering_point_ids=[]).values == {}


def test_gaps_differences_rounding_and_points_without_data_stand_out(leg):
    db, _, tmp = leg
    create_participant(db, Participant("test-new", "test-new"))
    create_meter(db, Meter("CH1000000000000000000000000000009", "test-new", "Neu", "consumer"))
    doc = _official_day()
    _with_consumer(doc, "local", {5: "1.502", 6: "1.501"})              # 5: wrong by 0.002, 6: rounding
    for s in doc.series:
        if s.point == PRODUCER:
            s.values = s.values[:50]
    _import(db, tmp, "e66.xml", doc)
    by_point = _points(period_sums(db, DAY, NEXT_DAY))
    assert (by_point[CONSUMER].check.mismatched, by_point[CONSUMER].check.rounded) == (1, 1)
    assert (by_point[PRODUCER].check.complete, by_point[PRODUCER].check.missing) == (50, 46)
    [day] = period_sums(db, DAY, NEXT_DAY).rows
    assert (day.points, day.incomplete_points) == (3, 2)                # producer and the silent meter
    silent = by_point["CH1000000000000000000000000000009"]
    assert silent.known and (silent.check.complete, silent.check.expected) == (0, 96)


def test_long_periods_are_broken_down_per_month_and_limited(leg):
    db, _, _ = leg
    year = period_sums(db, date(2026, 1, 1), date(2027, 1, 1))
    assert year.row_unit == "month" and len(year.rows) == 12
    with pytest.raises(ValueError):
        period_sums(db, date(2026, 1, 1), date(2027, 1, 3))
    with pytest.raises(ValueError):
        period_sums(db, DAY, DAY)


# ── values ──


def test_every_quarter_hour_has_a_row_also_without_data(leg):
    db, _, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    values = point_values(db, CONSUMER, DAY - timedelta(days=1), DAY + timedelta(days=2), today=NEXT_DAY)
    assert len(values.rows) == 3 * 96
    before, day, after = values.rows[:96], values.rows[96:192], values.rows[192:]
    assert not any(r.has_values for r in before) and all(r.delivered for r in before)   # "kein Wert geliefert"
    assert all(r.has_values for r in day) and day[0].start.strftime("%d.%m. %H:%M") == "01.12. 00:00"
    assert not any(r.delivered for r in after)                                       # "noch nicht geliefert"
    assert day[0].flows.consumption_total == Decimal("2.000") and day[0].flows.production_total == 0
    assert (values.total_check.expected, values.total_check.complete) == (192, 96)


def test_hour_and_day_resolution_and_direction_filter(leg):
    db, _, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    hours = point_values(db, PRODUCER, DAY, NEXT_DAY, resolution="hour", today=NEXT_DAY)
    assert len(hours.rows) == 24 and hours.rows[0].flows.production_total == Decimal("12.000")
    [day] = point_values(db, PRODUCER, DAY, NEXT_DAY, resolution="day", today=NEXT_DAY).rows
    assert day.flows.production_local == Decimal("144.000") and day.check.complete == 96
    only_drawn = point_values(db, PRODUCER, DAY, NEXT_DAY, directions=("consumption",), today=NEXT_DAY)
    assert not any(r.has_values for r in only_drawn.rows) and only_drawn.total.production_total == 0
    with pytest.raises(ValueError):
        point_values(db, PRODUCER, DAY, DAY + timedelta(days=32), today=NEXT_DAY)


def test_clock_change_day_has_92_quarter_hours_marked_with_the_zone(leg):
    db, _, _ = leg
    values = point_values(db, CONSUMER, date(2027, 3, 28), date(2027, 3, 29), today=date(2027, 4, 1))
    assert len(values.rows) == 92
    assert values.rows[0].zone == "MEZ" and values.rows[-1].zone == "MESZ"
    assert point_values(db, CONSUMER, DAY, NEXT_DAY, today=NEXT_DAY).rows[0].zone == ""


def test_origin_shows_every_delivery_and_which_value_counts(leg):
    db, _, tmp = leg
    _import(db, tmp, "daily-1.xml", _official_day(document_id="D1"))
    newer = _with_consumer(_official_day(document_id="D2", created=datetime(2026, 12, 8, 5, tzinfo=timezone.utc)),
                           "local", {5: "1.600"})
    _import(db, tmp, "daily-2.xml", newer)
    slot = START + timedelta(minutes=15 * 5)
    origin = quarter_hour_origin(db, CONSUMER, slot)
    first, second = origin.deliveries
    assert (first.file_name, second.file_name) == ("daily-1.xml", "daily-2.xml")
    assert ("consumption", "local") in second.in_use and ("consumption", "local") in second.changed
    assert ("consumption", "local") not in first.in_use
    assert origin.effective[("consumption", "local")][0] == Decimal("1.600")


def test_origin_keeps_a_plausibilised_value_against_a_later_temporary_one(leg):
    db, _, tmp = leg
    _import(db, tmp, "month.xml", _official_day(document_id="M1"))
    _import(db, tmp, "late.xml", _with_consumer(
        _official_day(document_id="L1", conditions=["21"] * SLOTS, created=datetime(2026, 12, 9, tzinfo=timezone.utc)),
        "total", {0: "9.000"}))
    first, second = quarter_hour_origin(db, CONSUMER, START).deliveries
    assert ("consumption", "total") in first.in_use and ("consumption", "total") not in second.in_use


# ── pages ──


def test_sums_page_links_every_number_to_the_values(leg):
    db, _, tmp = leg
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE participants SET label = '<b>Haus</b>' WHERE participant_id = 'test-load'")
    _import(db, tmp, "e66.xml", _official_day())
    html = analysis.handle_get(_get("sums", zeitraum="monat", datum="2026-12-15"))
    assert "Dezember 2026" in html and "192.000" in html and "Di 01.12.2026" in html
    assert "&lt;b&gt;Haus&lt;/b&gt;" in html and "<b>Haus</b>" not in html
    assert f"/analysis/values?mp={CONSUMER}&amp;zeitraum=frei&amp;von=2026-12-01&amp;bis=2026-12-31" in html
    assert "werte=bezug" in html and "werte=einspeisung" in html
    assert "✓ 96 / 96" in html and "Daten bis 01.12.2026" in html


@pytest.mark.parametrize("query", [
    {"zeitraum": "<script>", "datum": "2026-02-30", "mp": "<script>alert(1)</script>", "sort": "x"},
    {"zeitraum": "frei", "von": "2026-12-05", "bis": "2026-12-01"},
    {"zeitraum": "frei", "von": "2020-01-01", "bis": "2026-12-01"},
])
def test_sums_page_refuses_bad_filters_quietly(leg, query):
    html = analysis.handle_get(_get("sums", **query))
    assert "<script>" not in html and "Summen je Messpunkt" in html


def test_values_page_with_gaps_paging_and_origin(leg):
    db, _, tmp = leg
    _import(db, tmp, "e66.xml", _official_day())
    query = {"mp": CONSUMER, "zeitraum": "frei", "von": "2026-11-30", "bis": "2026-12-01", "zeilen": "50"}
    html = analysis.handle_get(_get("values", **query))
    assert "kein Wert geliefert" in html and "192 Zeile(n)" in html and "Seite 1 von 4" in html
    slot = (START + timedelta(minutes=15 * 3)).isoformat()
    detail = analysis.handle_get(_get("values", **query, seite="3", detail=slot))
    assert "Herkunft" in detail and "e66.xml" in detail and "gilt" in detail
    assert "Σ Summen dieses Zeitraums" in detail
    no_gaps = analysis.handle_get(_get("values", **query, luecken="0"))
    assert "96 Zeile(n)" in no_gaps and "kein Wert geliefert" not in no_gaps


@pytest.mark.parametrize("query", [
    {"mp": "'; DROP TABLE x; --", "zeitraum": "?", "aufl": "1s", "zeilen": "7", "seite": "-3", "werte": "z"},
    {"mp": CONSUMER, "detail": "not-a-time"},
    {"mp": CONSUMER, "detail": "2026-12-01T00:07:00+00:00"},
    {"mp": CONSUMER, "zeitraum": "frei", "von": "2026-01-01", "bis": "2026-12-31", "aufl": "15min"},
])
def test_values_page_refuses_bad_filters_quietly(leg, query):
    html = analysis.handle_get(_get("values", **query))
    assert "Messwerte" in html and "DROP TABLE" not in html and "Herkunft ·" not in html


def test_long_period_at_15_minutes_switches_to_days(leg):
    html = analysis.handle_get(_get("values", mp=CONSUMER, zeitraum="frei", von="2026-01-01", bis="2026-12-31",
                                    aufl="15min"))
    assert "Auflösung «Tag»" in html


def test_menu_and_messdaten_point_to_the_analysis(leg):
    db, _, tmp = leg
    energy = next(g for g in NAV_GROUPS if g.key == "energy")
    assert ("ANALYSE", (("analysis", "Übersicht"), ("analysis_sums", "Summen & Auswertung"),
                        ("analysis_values", "Messwerte"))) in energy.sections
    _import(db, tmp, "e66.xml", _official_day())
    html = meter_data.handle_get(RequestContext(method="GET", ingress_path="", query={}))
    assert "bis <strong>01.12.2026</strong>" in html and "/analysis/sums" in html
    assert "bisherigen Messdateien" in html
