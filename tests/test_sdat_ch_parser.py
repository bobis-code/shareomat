# -*- coding: utf-8 -*-
"""SDAT-CH 2025 E66/E31 parser (SX-2): structure, codes, time series, limits, safety."""
from __future__ import annotations

import gzip
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from shareomat.core.pipeline.raw import sdat_ch
from shareomat.core.pipeline.raw.sdat_ch import SdatChValidationError, parse_sdat_ch_document, read_sdat_bytes
from tests.sdat_fixtures import (
    COMMUNITY,
    CONSUMER,
    OTHER_COMMUNITY,
    PRODUCER,
    RECEIVER_EIC,
    Document,
    Series,
    build,
    day_start,
    leg_day,
)

FOUR = ["1.000", "2.000", "3.000", "4.000"]


def _code(data: bytes) -> str:
    with pytest.raises(SdatChValidationError) as err:
        parse_sdat_ch_document(data)
    return err.value.code


def test_e66_header_and_six_flows():
    doc = parse_sdat_ch_document(build(Document(series=leg_day(FOUR, FOUR, FOUR, ["0.500"] * 4))))
    h = doc.header
    assert (h.document_type, h.status, h.receiver_eic, h.receiver_role, h.business_reason) == ("E66", "9", RECEIVER_EIC, "CEM", "C40")
    assert h.acknowledgement_requested is True
    assert {(s.metering_point_id, s.direction, s.energy_type) for s in doc.series} == {
        (CONSUMER, "consumption", "total"), (CONSUMER, "consumption", "local"), (CONSUMER, "consumption", "residual"),
        (PRODUCER, "production", "total"), (PRODUCER, "production", "local"), (PRODUCER, "production", "residual"),
    }
    local = next(s for s in doc.series if s.direction == "production" and s.energy_type == "local")
    assert local.values == (Decimal("0.500"),) * 4 and local.community_id == COMMUNITY


def test_e31_aggregates_by_flow_characteristic():
    series = [Series("12Y-TESTAREA000X", "consumption", "total", FOUR), Series("12Y-TESTAREA000X", "production", "local", FOUR)]
    doc = parse_sdat_ch_document(build(Document(document_type="E31", series=series)))
    assert doc.header.document_type == "E31"
    assert [(s.grid_area_eic, s.direction, s.energy_type, s.metering_point_id) for s in doc.series] == [
        ("12Y-TESTAREA000X", "consumption", "total", ""), ("12Y-TESTAREA000X", "production", "local", ""),
    ]


def test_positions_become_quarter_hours_from_interval_start():
    start = datetime(2026, 12, 1, 23, 0, tzinfo=timezone.utc)
    doc = parse_sdat_ch_document(build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR, start=start)])))
    readings = [(start + timedelta(minutes=15 * i), v) for i, v in enumerate(doc.series[0].values)]
    assert readings[-1] == (datetime(2026, 12, 1, 23, 45, tzinfo=timezone.utc), Decimal("4.000"))


@pytest.mark.parametrize("local_day,count", [(datetime(2026, 3, 29), 92), (datetime(2026, 10, 25), 100), (datetime(2026, 6, 1), 96)])
def test_dst_days_have_92_100_or_96_quarter_hours(local_day, count):
    start = day_start(local_day)
    data = build(Document(series=[Series(CONSUMER, "consumption", "total", ["1.000"] * count, start=start)]))
    series = parse_sdat_ch_document(data).series[0]
    assert len(series.values) == count
    assert series.interval_end == day_start(local_day + timedelta(days=1))


def test_conditions_temporary_and_estimated_are_kept():
    data = build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR, ["21", "56", "", ""])]))
    assert parse_sdat_ch_document(data).series[0].conditions == ("21", "56", "", "")


def test_several_communities_in_one_document_are_allowed():
    """The official VSE examples 1_12_3/1_13_3 carry two communities in one document."""
    series = leg_day(FOUR, FOUR, FOUR, FOUR) + leg_day(FOUR, FOUR, FOUR, FOUR, community=OTHER_COMMUNITY)
    for s in series[6:]:
        s.point = s.point[:-1] + "9"
    assert parse_sdat_ch_document(build(Document(series=series))).community_ids == {COMMUNITY, OTHER_COMMUNITY}


# ── refusals ──


def test_missing_quarter_hour_is_refused():
    data = build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR)]))
    data = data.replace(b"<rsm:Observation><rsm:Position><rsm:Sequence>3</rsm:Sequence></rsm:Position><rsm:Volume>3.000</rsm:Volume></rsm:Observation>", b"")
    assert _code(data) == "INCOMPLETE_SERIES"


def test_duplicate_position_is_refused():
    data = build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR)]))
    data = data.replace(b"<rsm:Sequence>3</rsm:Sequence>", b"<rsm:Sequence>2</rsm:Sequence>")
    assert _code(data) == "DUPLICATE_POSITION"


@pytest.mark.parametrize("value,code", [("-1.000", "INVALID_VOLUME"), ("1.0001", "INVALID_VOLUME"), ("abc", "INVALID_VOLUME"), ("NaN", "INVALID_VOLUME")])
def test_invalid_volumes_are_refused(value, code):
    assert _code(build(Document(series=[Series(CONSUMER, "consumption", "total", [value, "1", "1", "1"])]))) == code


def test_unknown_condition_is_refused():
    assert _code(build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR, ["99", "", "", ""])]))) == "INVALID_CONDITION"


def test_foreign_product_is_refused():
    s = Series(CONSUMER, "consumption", "total", FOUR, product_override=("ebIXCode", "9", "8716867000047"))
    assert _code(build(Document(series=[s]))) == "UNSUPPORTED_PRODUCT"


def test_wrong_unit_and_resolution_are_refused():
    base = build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR)]))
    assert _code(base.replace(b"<rsm:MeasureUnit>KWH", b"<rsm:MeasureUnit>MWH")) == "UNSUPPORTED_UNIT"
    assert _code(base.replace(b"<rsm:Resolution>15</rsm:Resolution>", b"<rsm:Resolution>60</rsm:Resolution>")) == "UNSUPPORTED_RESOLUTION"


def test_same_flow_twice_is_ambiguous_and_refused():
    """Like the VSE example 1_12_3, blocks f/g: the product 'total' three times for one production point."""
    series = [Series(PRODUCER, "production", "total", FOUR), Series(PRODUCER, "production", "total", FOUR)]
    assert _code(build(Document(series=series))) == "DUPLICATE_SERIES"


def test_invalid_community_and_metering_point_are_refused():
    assert _code(build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR, community="LEG-TEST")]))) == "INVALID_COMMUNITY"
    assert _code(build(Document(series=[Series("12345", "consumption", "total", FOUR)]))) == "INVALID_METERING_POINT"


def test_wrong_document_type_or_status_is_refused():
    assert _code(build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR)], status="7"))) == "UNSUPPORTED_STATUS"
    data = build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR)]))
    assert _code(data.replace(b"<rsm:ebIXCode>E66</rsm:ebIXCode>", b"<rsm:ebIXCode>E31</rsm:ebIXCode>")) == "WRONG_DOCUMENT_TYPE"


def test_series_outside_report_period_is_refused():
    start = datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)
    doc = Document(series=[Series(CONSUMER, "consumption", "total", FOUR, start=start)],
                   report_start=start + timedelta(hours=1), report_end=start + timedelta(days=1))
    assert _code(build(doc)) == "OUTSIDE_REPORT_PERIOD"


def test_unaligned_interval_is_refused():
    start = datetime(2026, 11, 30, 23, 7, tzinfo=timezone.utc)
    assert _code(build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR, start=start)]))) == "INVALID_PERIOD"


def test_other_xml_is_not_an_sdat_document():
    assert _code(b'<?xml version="1.0"?><root/>') == "UNSUPPORTED_DOCUMENT"
    assert _code(b"not xml at all") == "INVALID_XML"


def test_dtd_and_entities_are_refused_before_parsing():
    bomb = (b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;">]>'
            b'<rsm:ValidatedMeteredData_16 xmlns:rsm="http://www.strom.ch">&lol2;</rsm:ValidatedMeteredData_16>')
    assert _code(bomb) == "FORBIDDEN_XML"
    xxe = (b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]>'
           b'<rsm:ValidatedMeteredData_16 xmlns:rsm="http://www.strom.ch">&e;</rsm:ValidatedMeteredData_16>')
    assert _code(xxe) == "FORBIDDEN_XML"


def test_gz_files_are_unpacked_with_a_limit(tmp_path, monkeypatch):
    data = build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR)]))
    path = tmp_path / "e66.xml.gz"
    path.write_bytes(gzip.compress(data))
    assert read_sdat_bytes(path) == data
    monkeypatch.setattr(sdat_ch, "MAX_DOCUMENT_BYTES", 100)
    with pytest.raises(SdatChValidationError) as err:
        read_sdat_bytes(path)
    assert err.value.code == "FILE_TOO_LARGE"


def test_broken_gz_is_unreadable(tmp_path):
    path = tmp_path / "e66.xml.gz"
    path.write_bytes(b"not gzip")
    with pytest.raises(SdatChValidationError) as err:
        read_sdat_bytes(path)
    assert err.value.code == "UNREADABLE_FILE"


def test_namespace_prefix_does_not_matter():
    data = build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR)]))
    data = data.replace(b"rsm:", b"x:").replace(b"xmlns:rsm=", b"xmlns:x=")
    assert parse_sdat_ch_document(data).series[0].values[0] == Decimal("1.000")


# ── official VSE examples (only where the ZIP was unpacked locally; not in the repo) ──

_VSE_DIR = os.environ.get("SHAREOMAT_VSE_EXAMPLES", "")


@pytest.mark.skipif(not _VSE_DIR or not Path(_VSE_DIR).is_dir(), reason="VSE SDAT-CH 2025 example ZIP not unpacked locally")
def test_official_vse_examples():
    folder = Path(_VSE_DIR)
    e66 = next(p for p in folder.iterdir() if "1_12_3" in p.name)
    e31 = next(p for p in folder.iterdir() if "1_13_3" in p.name)
    # The E66 example labels its blocks f/g with the product 'total' instead of LEG/residual
    # (copy error in the example): refused as ambiguous rather than guessed.
    with pytest.raises(SdatChValidationError) as err:
        parse_sdat_ch_document(read_sdat_bytes(e66))
    assert err.value.code == "DUPLICATE_SERIES"
    text = e66.read_text(encoding="utf-8")
    for block, code in (("f", "2404050010123"), ("g", "2404050010124")):
        i = text.index(f"Beispiel_Messdatenaustausch_1_12_3{block}<")
        old = '<rsm:ebIXCode schemeAgencyID="9">8716867000030</rsm:ebIXCode>'
        j = text.index(old, i)
        text = text[:j] + f'<rsm:VSENationalCode schemeAgencyID="260">{code}</rsm:VSENationalCode>' + text[j + len(old):]
    doc = parse_sdat_ch_document(text.encode("utf-8"))
    assert doc.header.receiver_role == "CEM" and doc.header.business_reason == "C40"
    assert len(doc.series) == 6 and doc.community_ids == {"999990-LEG001", "999990-LEG002"}
    by = {(s.direction, s.energy_type): s for s in doc.series}
    for direction in ("consumption", "production"):
        total, local, residual = (by[(direction, e)] for e in ("total", "local", "residual"))
        assert all(t == l + r for t, l, r in zip(total.values, local.values, residual.values))
    aggregate = parse_sdat_ch_document(read_sdat_bytes(e31))
    assert aggregate.header.document_type == "E31" and len(aggregate.series) == 6


def test_only_utf8_documents_are_parsed():
    data = build(Document(series=[Series(CONSUMER, "consumption", "total", FOUR)]))
    utf16 = data.decode("utf-8").replace('encoding="UTF-8"', 'encoding="UTF-16"').encode("utf-16")
    assert _code(utf16) == "UNSUPPORTED_ENCODING"
    latin = data.replace(b'encoding="UTF-8"', b'encoding="ISO-8859-1"')
    assert _code(latin) == "UNSUPPORTED_ENCODING"
    assert parse_sdat_ch_document(data.replace(b'encoding="UTF-8"', b'encoding="utf-8"')).series
