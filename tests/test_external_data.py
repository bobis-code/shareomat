# -*- coding: utf-8 -*-
"""
Tests for shareomat.external_data.elcom and shareomat.external_data.market_forecast.

Network access is mocked so the suite stays fast, offline, and
deterministic — the response payloads below are the real shapes captured
from live requests during development (see module docstrings for the
verified endpoints), not invented ones.
"""

from __future__ import annotations

import json
from decimal import Decimal
from io import BytesIO

import pytest

from shareomat.external_data import elcom, market_forecast


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


# ── ElCom ────────────────────────────────────────────────────────────────


_ELCOM_SAMPLE_RESPONSE = {
    "results": {
        "bindings": [
            {
                "category": {"type": "literal", "value": "H4"},
                "energy": {"type": "literal", "value": "13.456"},
                "grid": {"type": "literal", "value": "14.108"},
                "aidfee": {"type": "literal", "value": "2.300"},
                "community_fees": {"type": "literal", "value": "2.130"},
                "fixcosts": {"type": "literal", "value": "104.000"},
                "variablecosts": {"type": "literal", "value": "31.994"},
                "operator": {"type": "uri", "value": "https://energy.ld.admin.ch/elcom/electricityprice/operator/2"},
            },
        ],
    },
}

_ELCOM_OPERATOR_NAME_RESPONSE = {
    "results": {"bindings": [{"name": {"type": "literal", "value": "AGE SA"}}]},
}


def test_download_elcom_tariffs_converts_rappen_to_chf(monkeypatch):
    responses = [_ELCOM_SAMPLE_RESPONSE, _ELCOM_OPERATOR_NAME_RESPONSE]

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps(responses.pop(0)).encode("utf-8"))

    monkeypatch.setattr(elcom.urllib.request, "urlopen", fake_urlopen)

    tariffs = elcom.download_elcom_tariffs(5250, 2024)
    assert len(tariffs) == 1
    assert tariffs[0].category == "H4"
    assert tariffs[0].energy_chf_kwh == Decimal("0.13456")  # 13.456 Rp -> CHF
    assert tariffs[0].operator_name == "AGE SA"


def test_get_elcom_tariff_returns_none_for_missing_category(monkeypatch):
    responses = [_ELCOM_SAMPLE_RESPONSE, _ELCOM_OPERATOR_NAME_RESPONSE]

    def fake_urlopen(request, timeout=None):
        return _FakeResponse(json.dumps(responses.pop(0)).encode("utf-8"))

    monkeypatch.setattr(elcom.urllib.request, "urlopen", fake_urlopen)
    assert elcom.get_elcom_tariff(5250, 2024, "C7") is None


def test_compare_with_supplier_tariff_flags_mismatch(monkeypatch):
    responses = [_ELCOM_SAMPLE_RESPONSE, _ELCOM_OPERATOR_NAME_RESPONSE]
    monkeypatch.setattr(
        elcom.urllib.request, "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps(responses.pop(0)).encode("utf-8")),
    )
    result = elcom.compare_with_supplier_tariff(5250, 2024, "H4", Decimal("0.20"))
    assert result["status"] == "ok"
    assert result["matches"] is False
    assert result["difference_chf_kwh"] == Decimal("0.06544")


def test_elcom_fetch_error_on_network_failure(monkeypatch):
    import urllib.error

    def raise_error(request, timeout=None):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(elcom.urllib.request, "urlopen", raise_error)
    with pytest.raises(elcom.ElcomFetchError):
        elcom.download_elcom_tariffs(5250, 2024)


# ── SNB exchange rates ──────────────────────────────────────────────────────


_SNB_SAMPLE_RESPONSE = {
    "timeseries": [
        {
            "header": [{"dim": "Currency", "dimItem": "Europe - EUR 1"}],
            "values": [
                {"date": "2026-01", "value": 0.92732},
                {"date": "2026-02", "value": 0.91406},
            ],
        },
        {
            "header": [{"dim": "Currency", "dimItem": "Europe - United Kingdom GBP 1"}],
            "values": [{"date": "2026-01", "value": 1.06864}],
        },
    ],
}


def test_download_exchange_rates_filters_eur_series(monkeypatch):
    monkeypatch.setattr(
        market_forecast.urllib.request, "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps(_SNB_SAMPLE_RESPONSE).encode("utf-8")),
    )
    rates = market_forecast.download_exchange_rates("2026-01", "2026-02")
    assert len(rates) == 2
    assert rates[0].period == "2026-01"
    assert rates[0].rate_chf_per_eur == Decimal("0.92732")


def test_download_exchange_rates_empty_when_no_eur_series(monkeypatch):
    no_eur = {"timeseries": [_SNB_SAMPLE_RESPONSE["timeseries"][1]]}
    monkeypatch.setattr(
        market_forecast.urllib.request, "urlopen",
        lambda request, timeout=None: _FakeResponse(json.dumps(no_eur).encode("utf-8")),
    )
    assert market_forecast.download_exchange_rates("2026-01", "2026-02") == []


# ── ENTSO-E day-ahead prices ─────────────────────────────────────────────────


_ENTSOE_SAMPLE_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Publication_MarketDocument xmlns="urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3">
  <TimeSeries>
    <Period>
      <timeInterval><start>2026-07-01T00:00Z</start><end>2026-07-02T00:00Z</end></timeInterval>
      <resolution>PT60M</resolution>
      <Point><position>1</position><price.amount>45.30</price.amount></Point>
      <Point><position>2</position><price.amount>42.10</price.amount></Point>
    </Period>
  </TimeSeries>
</Publication_MarketDocument>
"""


def test_download_day_ahead_prices_parses_points(monkeypatch):
    monkeypatch.setattr(
        market_forecast.urllib.request, "urlopen",
        lambda request, timeout=None: _FakeResponse(_ENTSOE_SAMPLE_XML),
    )
    from datetime import datetime
    prices = market_forecast.download_day_ahead_prices(
        datetime(2026, 7, 1), datetime(2026, 7, 2), api_token="fake-token",
    )
    assert len(prices) == 2
    assert prices[0].price_eur_mwh == Decimal("45.30")
    assert prices[1].start.hour == 1  # position 2 -> second hour of the period


def test_download_day_ahead_prices_requires_token():
    with pytest.raises(market_forecast.MarketForecastError, match="Token"):
        from datetime import datetime
        market_forecast.download_day_ahead_prices(datetime(2026, 7, 1), datetime(2026, 7, 2), api_token="")


def test_entsoe_http_error_is_wrapped(monkeypatch):
    import urllib.error

    def raise_http_error(request, timeout=None):
        raise urllib.error.HTTPError(
            url="https://web-api.tp.entsoe.eu/api", code=401, msg="Unauthorized",
            hdrs=None, fp=BytesIO(b"Unauthorized. Missing or invalid security token."),
        )

    monkeypatch.setattr(market_forecast.urllib.request, "urlopen", raise_http_error)
    from datetime import datetime
    with pytest.raises(market_forecast.MarketForecastError, match="401"):
        market_forecast.download_day_ahead_prices(
            datetime(2026, 7, 1), datetime(2026, 7, 2), api_token="bad-token",
        )
