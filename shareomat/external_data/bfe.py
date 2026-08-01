# -*- coding: utf-8 -*-
"""
File: shareomat/external_data/bfe.py

Purpose:
    Fetch the official BFE (Bundesamt für Energie) quarterly/monthly PV
    reference market price (Art. 15 EnFV) — the authoritative value for
    final feed-in settlement, as opposed to the running, non-official
    forecast in shareomat.external_data.market_forecast.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Official dataset: BFE-DS-0020 "Referenz-Marktpreise gemäss Art. 15
    EnFV". Metadata + live resource structure verified on 2026-08-01:

      CKAN metadata:
        https://ckan.opendata.swiss/api/3/action/package_show
            ?id=referenz-marktpreise-gemass-art-15-enfv
      Resources (identifier -> URL, both confirmed live and parsed):
        BFE-B-0072  ogd60_rmp_monatspreise.csv    (monthly, CSV)
        BFE-B-0071  ogd60_rmp_quartalspreise.csv  (quarterly, CSV)
        BFE-B-0074  ogd60_rmp_monatspreise.xml    (monthly, XML)
        BFE-B-0073  ogd60_rmp_quartalspreise.xml  (quarterly, XML)

    CSV columns (utf-8-sig, comma-separated):
        Year,Period|Month,Days,
        Volume_pv_MWh,Price_pv_CHF_MWh,
        Volume_wasserkraft_MWh,Price_wasserkraft_CHF_MWh,
        Volume_windenergie_MWh,Price_windenergie_CHF_MWh,
        Volume_biomasse_MWh,Price_biomasse_CHF_MWh
    Prices are CHF/MWh; converted to CHF/kWh here to match
    shareomat.models.tariff.Tariff / ReferencePrice.

    No i14y HTML scraping. The CKAN download_url is resolved fresh on
    every call (the URL is versioned metadata, not hardcoded), falling
    back to the known static URLs above if the CKAN API itself is
    unreachable. CSV is tried first; XML only if CSV fails.

    "Keep last successful data on transient failure" is handled by the
    caller, not here: shareomat.web.pages.external_data only overwrites
    shareomat.database.reference_prices on a successful fetch, so a
    failed fetch simply leaves the previously stored (and displayed)
    values in place — this module always either returns fresh data or
    raises, it never invents or caches data.

    get_minimum_feed_in_price() is NOT part of this dataset (the
    statutory minimum feed-in tariff is a separate regulatory value, not
    published in the Art. 15 EnFV reference-price files) and remains
    unimplemented until a real source for it is confirmed.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

_CKAN_PACKAGE_URL = (
    "https://ckan.opendata.swiss/api/3/action/package_show"
    "?id=referenz-marktpreise-gemass-art-15-enfv"
)

# Known-good direct URLs — used if the CKAN metadata lookup itself fails.
_FALLBACK_URLS = {
    ("monthly", "csv"): "https://www.bfe-ogd.ch/ogd60_rmp_monatspreise.csv",
    ("quarterly", "csv"): "https://www.bfe-ogd.ch/ogd60_rmp_quartalspreise.csv",
    ("monthly", "xml"): "https://www.bfe-ogd.ch/ogd60_rmp_monatspreise.xml",
    ("quarterly", "xml"): "https://www.bfe-ogd.ch/ogd60_rmp_quartalspreise.xml",
}
_IDENTIFIERS = {
    ("monthly", "csv"): "BFE-B-0072",
    ("quarterly", "csv"): "BFE-B-0071",
    ("monthly", "xml"): "BFE-B-0074",
    ("quarterly", "xml"): "BFE-B-0073",
}

_TECHNOLOGIES = ("pv", "wasserkraft", "windenergie", "biomasse")
_USER_AGENT = "Shareomat/1 (+https://github.com/bobis-code/shareomat)"
_TIMEOUT_SECONDS = 20
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = (1, 2, 4)


class BfeFetchError(Exception):
    """Raised when neither CKAN metadata nor the fallback URL yield usable BFE data."""


class BfeNotImplementedError(NotImplementedError):
    """Raised only by get_minimum_feed_in_price() — no confirmed source exists for that value."""


@dataclass
class BfeReferencePrice:
    """One BFE reference-market-price observation for one technology and period."""

    technology: str
    year: int
    period_label: str  # "Q1".."Q4" or "1".."12"
    period_start: date
    period_end: date
    price_chf_kwh: Decimal
    volume_mwh: Decimal


def _fetch_with_retries(url: str, *, accept: str) -> bytes:
    """GET a URL with a browser-identifying User-Agent, timeout, and retry/backoff.

    urllib follows redirects automatically via its default opener.
    """
    last_error: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": accept})
        try:
            with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
            logger.warning("BFE: fetch attempt %d/%d failed for %s: %s", attempt, _MAX_ATTEMPTS, url, exc)
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_RETRY_BACKOFF_SECONDS[attempt - 1])
    raise BfeFetchError(f"Abruf von {url} fehlgeschlagen nach {_MAX_ATTEMPTS} Versuchen: {last_error}")


def _resolve_download_url(period_type: str, file_format: str) -> str:
    """Resolve the current download_url via CKAN metadata; fall back to the known static URL."""
    key = (period_type, file_format)
    try:
        body = _fetch_with_retries(_CKAN_PACKAGE_URL, accept="application/json")
        payload = json.loads(body.decode("utf-8"))
        for resource in payload.get("result", {}).get("resources", []):
            if resource.get("identifier") == _IDENTIFIERS[key]:
                url = resource.get("download_url") or resource.get("url")
                if url:
                    return url
        logger.warning("BFE: identifier %s not found in CKAN metadata, using fallback URL", _IDENTIFIERS[key])
    except Exception as exc:
        logger.warning("BFE: CKAN metadata lookup failed (%s), using fallback URL", exc)
    return _FALLBACK_URLS[key]


def _quarter_bounds(year: int, label: str) -> tuple[date, date]:
    quarter = int(label.lstrip("Qq"))
    start_month = (quarter - 1) * 3 + 1
    end_month = start_month + 2
    end_day = 31 if end_month in (3, 12) else 30
    return date(year, start_month, 1), date(year, end_month, end_day)


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    import calendar
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def _parse_csv(raw: bytes, period_type: str) -> list[BfeReferencePrice]:
    text = raw.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    results: list[BfeReferencePrice] = []
    for row in reader:
        year = int(row["Year"])
        if period_type == "quarterly":
            label = row["Period"]
            period_start, period_end = _quarter_bounds(year, label)
        else:
            label = row["Month"]
            period_start, period_end = _month_bounds(year, int(label))

        for technology in _TECHNOLOGIES:
            price_raw = row.get(f"Price_{technology}_CHF_MWh")
            volume_raw = row.get(f"Volume_{technology}_MWh")
            if not price_raw:
                continue
            try:
                price_chf_mwh = Decimal(price_raw)
            except InvalidOperation:
                continue
            results.append(BfeReferencePrice(
                technology=technology, year=year, period_label=label,
                period_start=period_start, period_end=period_end,
                price_chf_kwh=price_chf_mwh / 1000,
                volume_mwh=Decimal(volume_raw) if volume_raw else Decimal(0),
            ))
    return results


def _parse_xml(raw: bytes, period_type: str) -> list[BfeReferencePrice]:
    root = ET.fromstring(raw.decode("utf-8-sig"))
    results: list[BfeReferencePrice] = []
    for item in root.findall("price_item"):
        year = int(item.findtext("year"))
        if period_type == "quarterly":
            label = item.findtext("period")
            period_start, period_end = _quarter_bounds(year, label)
        else:
            label = item.findtext("month")
            period_start, period_end = _month_bounds(year, int(label))

        for technology in _TECHNOLOGIES:
            tech_el = item.find(technology)
            if tech_el is None:
                continue
            price_text = tech_el.findtext("price_CHF_MWh")
            volume_text = tech_el.findtext("volume_MWh")
            if not price_text:
                continue
            try:
                price_chf_mwh = Decimal(price_text)
            except InvalidOperation:
                continue
            results.append(BfeReferencePrice(
                technology=technology, year=year, period_label=label,
                period_start=period_start, period_end=period_end,
                price_chf_kwh=price_chf_mwh / 1000,
                volume_mwh=Decimal(volume_text) if volume_text else Decimal(0),
            ))
    return results


def download_reference_prices(*, period_type: str = "quarterly") -> list[BfeReferencePrice]:
    """Fetch every technology/period from the official BFE reference-price dataset.

    Tries CSV first, then XML, both via a freshly resolved CKAN
    download_url (falling back to the known static URL if CKAN itself is
    unreachable). Raises BfeFetchError only if both formats fail.
    """
    if period_type not in ("monthly", "quarterly"):
        raise ValueError("period_type must be 'monthly' or 'quarterly'")

    errors: list[str] = []
    for file_format, parser in (("csv", _parse_csv), ("xml", _parse_xml)):
        url = _resolve_download_url(period_type, file_format)
        accept = "text/csv" if file_format == "csv" else "application/xml"
        try:
            raw = _fetch_with_retries(url, accept=accept)
            results = parser(raw, period_type)
            logger.info("BFE: fetched %d reference price row(s) from %s (%s)", len(results), url, file_format)
            return results
        except Exception as exc:
            errors.append(f"{file_format}: {exc}")
            logger.warning("BFE: %s parse failed, trying next format: %s", file_format, exc)

    raise BfeFetchError(f"BFE-Referenzpreise konnten nicht geladen werden ({'; '.join(errors)})")


def get_official_pv_reference_price(
    year: int, period_label: str, *, period_type: str = "quarterly",
) -> BfeReferencePrice | None:
    """Return the official PV reference market price for one specific period (e.g. year=2026, "Q1")."""
    for row in download_reference_prices(period_type=period_type):
        if row.technology == "pv" and row.year == year and row.period_label == period_label:
            return row
    return None


def get_minimum_feed_in_price(*_args, **_kwargs) -> None:
    """Return the statutory minimum feed-in price. Not implemented — see module docstring."""
    raise BfeNotImplementedError(
        "get_minimum_feed_in_price() ist nicht implementiert: die gesetzliche Mindestvergütung ist "
        "kein Teil des Referenz-Marktpreis-Datensatzes (BFE-DS-0020) und braucht eine eigene, noch "
        "unbestätigte Quelle."
    )
