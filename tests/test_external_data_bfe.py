# -*- coding: utf-8 -*-
"""
Tests for shareomat.external_data.bfe.

Network access is mocked so the suite stays fast, offline, and
deterministic. The CSV/XML sample content below is a trimmed excerpt of
the real, live-verified BFE-OGD resources (see the module docstring for
the confirmed URLs/identifiers).
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date
from decimal import Decimal

import pytest

from shareomat.external_data import bfe


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


_SAMPLE_CSV = (
    "﻿Year,Period,Days,Volume_pv_MWh,Price_pv_CHF_MWh,"
    "Volume_wasserkraft_MWh,Price_wasserkraft_CHF_MWh,"
    "Volume_windenergie_MWh,Price_windenergie_CHF_MWh,"
    "Volume_biomasse_MWh,Price_biomasse_CHF_MWh\n"
    "2024,Q1,91,378517,61.97,3514334,73.10,47679,69.61,445838,71.67\n"
    "2024,Q2,91,970095,35.07,5332657,57.30,37648,56.54,391026,56.57\n"
).encode("utf-8")

_SAMPLE_XML = (
    "<?xml version='1.0' encoding='UTF-8'?>\n"
    "<market_prices><price_item><year>2024</year><period>Q1</period>"
    "<pv><price_CHF_MWh>61.97</price_CHF_MWh><volume_MWh>378517</volume_MWh></pv>"
    "<wasserkraft><price_CHF_MWh>73.10</price_CHF_MWh><volume_MWh>3514334</volume_MWh></wasserkraft>"
    "<windenergie><price_CHF_MWh>69.61</price_CHF_MWh><volume_MWh>47679</volume_MWh></windenergie>"
    "<biomasse><price_CHF_MWh>71.67</price_CHF_MWh><volume_MWh>445838</volume_MWh></biomasse>"
    "<days>91</days></price_item></market_prices>"
).encode("utf-8")

_CKAN_METADATA = {
    "result": {
        "resources": [
            {"identifier": "BFE-B-0072", "download_url": "https://example.test/monthly.csv"},
            {"identifier": "BFE-B-0071", "download_url": "https://example.test/quarterly.csv"},
            {"identifier": "BFE-B-0074", "download_url": "https://example.test/monthly.xml"},
            {"identifier": "BFE-B-0073", "download_url": "https://example.test/quarterly.xml"},
        ],
    },
}


def test_parse_csv_converts_mwh_to_kwh_and_expands_quarter():
    rows = bfe._parse_csv(_SAMPLE_CSV, "quarterly")
    pv_q1 = next(r for r in rows if r.technology == "pv" and r.period_label == "Q1")
    assert pv_q1.price_chf_kwh == Decimal("61.97") / 1000
    assert pv_q1.period_start == date(2024, 1, 1)
    assert pv_q1.period_end == date(2024, 3, 31)
    assert len(rows) == 8  # 2 periods x 4 technologies


def test_parse_xml_matches_csv_values():
    csv_rows = bfe._parse_csv(_SAMPLE_CSV, "quarterly")
    xml_rows = bfe._parse_xml(_SAMPLE_XML, "quarterly")
    csv_pv = next(r for r in csv_rows if r.technology == "pv" and r.period_label == "Q1")
    xml_pv = next(r for r in xml_rows if r.technology == "pv" and r.period_label == "Q1")
    assert csv_pv.price_chf_kwh == xml_pv.price_chf_kwh
    assert csv_pv.period_start == xml_pv.period_start


def test_download_reference_prices_uses_ckan_resolved_url(monkeypatch):
    calls = []

    def fake_fetch(url, *, accept):
        calls.append(url)
        if url == bfe._CKAN_PACKAGE_URL:
            return json.dumps(_CKAN_METADATA).encode("utf-8")
        if url == "https://example.test/quarterly.csv":
            return _SAMPLE_CSV
        raise AssertionError(f"unexpected URL requested: {url}")

    monkeypatch.setattr(bfe, "_fetch_with_retries", fake_fetch)
    rows = bfe.download_reference_prices(period_type="quarterly")
    assert len(rows) == 8
    assert "https://example.test/quarterly.csv" in calls


def test_download_reference_prices_falls_back_to_static_url_when_ckan_fails(monkeypatch):
    def fake_fetch(url, *, accept):
        if url == bfe._CKAN_PACKAGE_URL:
            raise bfe.BfeFetchError("simulated CKAN outage")
        assert url == bfe._FALLBACK_URLS[("quarterly", "csv")]
        return _SAMPLE_CSV

    monkeypatch.setattr(bfe, "_fetch_with_retries", fake_fetch)
    rows = bfe.download_reference_prices(period_type="quarterly")
    assert len(rows) == 8


def test_download_reference_prices_falls_back_to_xml_when_csv_fails(monkeypatch):
    def fake_fetch(url, *, accept):
        if url == bfe._CKAN_PACKAGE_URL:
            raise bfe.BfeFetchError("simulated CKAN outage")
        if accept == "text/csv":
            raise bfe.BfeFetchError("simulated CSV fetch failure")
        return _SAMPLE_XML

    monkeypatch.setattr(bfe, "_fetch_with_retries", fake_fetch)
    rows = bfe.download_reference_prices(period_type="quarterly")
    assert len(rows) == 4  # _SAMPLE_XML has only one price_item (Q1) x 4 technologies


def test_download_reference_prices_raises_when_both_formats_fail(monkeypatch):
    def fake_fetch(url, *, accept):
        raise bfe.BfeFetchError("simulated total outage")

    monkeypatch.setattr(bfe, "_fetch_with_retries", fake_fetch)
    with pytest.raises(bfe.BfeFetchError):
        bfe.download_reference_prices(period_type="quarterly")


def test_get_official_pv_reference_price_filters_by_year_and_period(monkeypatch):
    monkeypatch.setattr(bfe, "download_reference_prices", lambda period_type="quarterly": bfe._parse_csv(_SAMPLE_CSV, "quarterly"))
    price = bfe.get_official_pv_reference_price(2024, "Q2")
    assert price is not None
    assert price.technology == "pv"
    assert price.price_chf_kwh == Decimal("35.07") / 1000
    assert bfe.get_official_pv_reference_price(2099, "Q1") is None


def test_get_minimum_feed_in_price_not_implemented():
    with pytest.raises(bfe.BfeNotImplementedError, match="Referenz-Marktpreis"):
        bfe.get_minimum_feed_in_price()


def test_fetch_with_retries_retries_then_raises(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout=None):
        attempts.append(1)
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(bfe.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(bfe.time, "sleep", lambda seconds: None)  # skip real backoff delay in tests

    with pytest.raises(bfe.BfeFetchError):
        bfe._fetch_with_retries("https://example.test/x.csv", accept="text/csv")
    assert len(attempts) == bfe._MAX_ATTEMPTS
