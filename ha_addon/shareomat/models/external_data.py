# -*- coding: utf-8 -*-
"""
File: shareomat/models/external_data.py

Purpose:
    Domain models for external data sources: supplier (grid operator)
    tariffs, official reference prices, computed price forecasts, and the
    audit trail of every external data fetch.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data containers — no business logic, no I/O. Grouped in one
    module (unlike Participant/Meter/Tariff) because they are all small,
    closely related pieces of the same external-data feature rather than
    independently manageable entities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass
class SupplierTariff:
    """Grid operator (e.g. EBL) tariff components, valid for a date range."""

    source: str  # e.g. "EBL"
    valid_from: date
    energy_rate_ht_chf_kwh: Decimal | None = None
    energy_rate_nt_chf_kwh: Decimal | None = None
    grid_rate_ht_chf_kwh: Decimal | None = None
    grid_rate_nt_chf_kwh: Decimal | None = None
    base_price_chf_year: Decimal | None = None
    metering_price_chf_year: Decimal | None = None
    feed_in_rate_chf_kwh: Decimal | None = None
    hkn_rate_chf_kwh: Decimal | None = None
    valid_until: date | None = None
    active: bool = True
    id: int | None = None


@dataclass
class ReferencePrice:
    """An official reference price for a period (e.g. BFE quarterly PV reference market price)."""

    technology: str  # e.g. "pv"
    period_start: date
    period_end: date
    price_chf_kwh: Decimal
    source: str  # e.g. "BFE"
    is_official: bool = True
    published_at: date | None = None
    id: int | None = None


@dataclass
class PriceForecast:
    """A computed (non-official) market price forecast for a period."""

    period_start: date
    period_end: date
    forecast_price_chf_kwh: Decimal
    sources: list[str]  # e.g. ["entsoe", "snb"]
    completeness_pct: Decimal | None = None
    computed_at: datetime | None = None
    id: int | None = None


@dataclass
class DayAheadPricePoint:
    """One native-resolution ENTSO-E day-ahead price point (CHF/kWh).

    Unlike PriceForecast (a single averaged value for a whole date range,
    dashboard-only), this preserves whatever resolution ENTSO-E actually
    publishes at (typically hourly, sometimes 15-minute) - for algorithmic
    consumption via Sparkplug LEG/ExportPrice
    (shareomat.sparkplug.coordinator_mapping), which must never see an
    artificially daily-aggregated or interpolated curve."""

    slot_start: datetime  # UTC
    price_chf_kwh: Decimal
    id: int | None = None


@dataclass
class ExternalDataSettings:
    """Credentials/settings for external data sources, entered via the web UI (never YAML)."""

    entsoe_api_token: str = ""
    municipality_bfs_number: str = ""  # default municipality for the ElCom tariff lookup


@dataclass
class DataImportRecord:
    """One audit-trail entry for an external data fetch attempt."""

    source: str  # "EBL" | "ElCom" | "BFE" | "ENTSO-E" | "SNB"
    data_type: str  # e.g. "tariffs" | "day_ahead_prices" | "exchange_rates"
    status: str  # "ok" | "error"
    fetched_at: datetime | None = None
    valid_from: date | None = None
    detail: str = ""
    checksum: str = ""
    id: int | None = None
