# -*- coding: utf-8 -*-
"""
File: shareomat/external_data/market_forecast.py

Purpose:
    Inputs for a non-official PV reference-price forecast: SNB EUR/CHF
    exchange rates (fully working, no credentials needed) and ENTSO-E
    day-ahead prices / generation profiles (needs a personal API token —
    see get_entsoe_api_token() in shareomat.database.external_settings).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    This forecast is for the dashboard and tariff-planning only — never
    for final settlement. Use shareomat.database.reference_prices (BFE's
    official quarterly value) for that. See
    SHAREOMAT_UMBAU_STRUKTUR-style docs / conversation notes for the
    rationale.

    Endpoints verified live on 2026-08-01:
      - SNB: https://data.snb.ch/api/cube/devkum (monthly EUR/CHF rate,
        cube "devkua" for annual) — public, no authentication, full
        response with real values confirmed.
      - ENTSO-E: https://web-api.tp.entsoe.eu/api — confirmed reachable
        with the exact parameter set used below (day-ahead prices:
        documentType=A44; generation: documentType=A75/processType=A16),
        domain 10YCH-SWISSGRIDZ for Switzerland. Both calls returned the
        expected "Unauthorized: Missing or invalid security token" error
        with a placeholder token, confirming the request shape is
        accepted — but the actual data shape could not be verified
        end-to-end without a real token.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

logger = logging.getLogger(__name__)

_SNB_TIMEOUT_SECONDS = 20
_ENTSOE_TIMEOUT_SECONDS = 20
_ENTSOE_BASE_URL = "https://web-api.tp.entsoe.eu/api"
_ENTSOE_SWITZERLAND_DOMAIN = "10YCH-SWISSGRIDZ"
_USER_AGENT = "Shareomat/1 (+https://github.com/bobis-code/shareomat)"

# ENTSO-E XML documents use this namespace for both day-ahead prices and generation.
_ENTSOE_NS = {
    "ns": "urn:iec62325.351:tc57wg16:451-3:publicationdocument:7:3",
}


class MarketForecastError(Exception):
    """Raised when SNB or ENTSO-E data cannot be fetched or parsed."""


@dataclass
class ExchangeRate:
    """One EUR/CHF rate for a period (monthly average, per the SNB "devkum" cube)."""

    period: str  # "YYYY-MM"
    rate_chf_per_eur: Decimal


@dataclass
class DayAheadPrice:
    """One hourly day-ahead price point in EUR/MWh, as published by ENTSO-E."""

    start: datetime
    price_eur_mwh: Decimal


# ── SNB — EUR/CHF exchange rate (public, no token) ─────────────────────────


def download_exchange_rates(from_period: str, to_period: str) -> list[ExchangeRate]:
    """Fetch monthly EUR/CHF rates from the SNB data portal for the ["YYYY-MM", "YYYY-MM"] range.

    The API returns every tracked currency regardless of query
    parameters (dimension-selection query syntax could not be confirmed
    reliably) — so this filters the EUR series out of the full response
    client-side instead.
    """
    url = (
        f"https://data.snb.ch/api/cube/devkum/data/json/en"
        f"?fromDate={urllib.parse.quote(from_period)}&toDate={urllib.parse.quote(to_period)}"
    )
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_SNB_TIMEOUT_SECONDS) as response:
            import json
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MarketForecastError(f"SNB-Abfrage fehlgeschlagen: {exc}") from exc

    for series in payload.get("timeseries", []):
        dim_items = [h.get("dimItem", "") for h in series.get("header", [])]
        if any("EUR" in item for item in dim_items):
            rates = [
                ExchangeRate(period=v["date"], rate_chf_per_eur=Decimal(str(v["value"])))
                for v in series.get("values", [])
            ]
            logger.info("SNB: fetched %d EUR/CHF rate(s) for %s..%s", len(rates), from_period, to_period)
            return rates

    logger.warning("SNB: no EUR series found in response for %s..%s", from_period, to_period)
    return []


# ── ENTSO-E — day-ahead prices / generation (needs a personal API token) ───


def _entsoe_request(params: dict[str, str], api_token: str) -> ET.Element:
    query = dict(params)
    query["securityToken"] = api_token
    url = f"{_ENTSOE_BASE_URL}?{urllib.parse.urlencode(query)}"
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=_ENTSOE_TIMEOUT_SECONDS) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise MarketForecastError(f"ENTSO-E-Abfrage fehlgeschlagen ({exc.code}): {detail[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise MarketForecastError(f"ENTSO-E-Abfrage fehlgeschlagen: {exc}") from exc

    try:
        return ET.fromstring(body)
    except ET.ParseError as exc:
        raise MarketForecastError(f"ENTSO-E-Antwort konnte nicht gelesen werden: {exc}") from exc


def download_day_ahead_prices(period_start: datetime, period_end: datetime, *, api_token: str) -> list[DayAheadPrice]:
    """Fetch hourly day-ahead prices for Switzerland (EUR/MWh) from ENTSO-E.

    Requires a personal API token — see shareomat.database.external_settings.
    """
    if not api_token:
        raise MarketForecastError("Kein ENTSO-E-API-Token hinterlegt (siehe Externe Daten in den Einstellungen).")

    root = _entsoe_request({
        "documentType": "A44",
        "in_Domain": _ENTSOE_SWITZERLAND_DOMAIN,
        "out_Domain": _ENTSOE_SWITZERLAND_DOMAIN,
        "periodStart": period_start.strftime("%Y%m%d%H%M"),
        "periodEnd": period_end.strftime("%Y%m%d%H%M"),
    }, api_token)

    results: list[DayAheadPrice] = []
    for time_series in root.findall(".//ns:TimeSeries", _ENTSOE_NS):
        period = time_series.find("ns:Period", _ENTSOE_NS)
        if period is None:
            continue
        start_text = period.findtext("ns:timeInterval/ns:start", namespaces=_ENTSOE_NS)
        if not start_text:
            continue
        period_start_dt = datetime.strptime(start_text, "%Y-%m-%dT%H:%MZ")
        resolution = period.findtext("ns:resolution", default="PT60M", namespaces=_ENTSOE_NS)
        step_minutes = 60 if resolution == "PT60M" else 15

        for point in period.findall("ns:Point", _ENTSOE_NS):
            position = int(point.findtext("ns:position", namespaces=_ENTSOE_NS))
            price = point.findtext("ns:price.amount", namespaces=_ENTSOE_NS)
            if price is None:
                continue
            from datetime import timedelta
            results.append(DayAheadPrice(
                start=period_start_dt + timedelta(minutes=step_minutes * (position - 1)),
                price_eur_mwh=Decimal(price),
            ))

    logger.info("ENTSO-E: fetched %d day-ahead price point(s)", len(results))
    return results


def download_generation_profiles(period_start: datetime, period_end: datetime, *, api_token: str) -> ET.Element:
    """Fetch actual generation-per-type for Switzerland from ENTSO-E, as raw parsed XML.

    Structurally verified (parameter shape accepted by the API), but the
    XML shape has not been verified end-to-end without a real token —
    calling code should treat the return value as provisional until a
    real token confirms it.
    """
    if not api_token:
        raise MarketForecastError("Kein ENTSO-E-API-Token hinterlegt (siehe Externe Daten in den Einstellungen).")

    return _entsoe_request({
        "documentType": "A75",
        "processType": "A16",
        "in_Domain": _ENTSOE_SWITZERLAND_DOMAIN,
        "periodStart": period_start.strftime("%Y%m%d%H%M"),
        "periodEnd": period_end.strftime("%Y%m%d%H%M"),
    }, api_token)


# ── Combined forecast ───────────────────────────────────────────────────────


def calculate_pv_reference_price_forecast(
    period_start: date, period_end: date, *, entsoe_api_token: str,
) -> dict[str, object]:
    """Combine ENTSO-E day-ahead prices and the SNB EUR/CHF rate into a rough PV price forecast.

    This is a simplified, non-official estimate — average day-ahead price
    for the period, converted to CHF/kWh using the period's average
    EUR/CHF rate. Real PV-weighted forecasting (weighting each hour's
    price by that hour's generation) needs download_generation_profiles(),
    which is provisional until tested against a real token.
    """
    from datetime import time as _time

    prices = download_day_ahead_prices(
        datetime.combine(period_start, _time.min), datetime.combine(period_end, _time.min),
        api_token=entsoe_api_token,
    )
    rates = download_exchange_rates(
        period_start.strftime("%Y-%m"), period_end.strftime("%Y-%m"),
    )

    if not prices:
        return {"status": "no_data", "sources": ["entsoe"]}

    avg_price_eur_mwh = sum(p.price_eur_mwh for p in prices) / len(prices)
    avg_rate = (
        sum(r.rate_chf_per_eur for r in rates) / len(rates)
        if rates else Decimal("1.0")  # neutral fallback if SNB has no data yet for the period
    )
    forecast_chf_kwh = (avg_price_eur_mwh * avg_rate) / Decimal(1000)

    completeness = Decimal(len(prices)) / Decimal(max((period_end - period_start).days * 24, 1)) * 100

    return {
        "status": "ok",
        "period_start": period_start,
        "period_end": period_end,
        "forecast_price_chf_kwh": forecast_chf_kwh,
        "completeness_pct": min(completeness, Decimal(100)),
        "sources": ["entsoe", "snb"] if rates else ["entsoe"],
        "sample_count": len(prices),
    }
