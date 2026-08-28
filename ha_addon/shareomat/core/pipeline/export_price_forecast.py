# -*- coding: utf-8 -*-
"""
File: shareomat/core/pipeline/export_price_forecast.py

Purpose:
    Fetch ENTSO-E day-ahead prices at their NATIVE resolution (typically
    hourly, sometimes 15-minute - never artificially aggregated to a
    single daily value like shareomat.external_data.market_forecast.
    calculate_pv_reference_price_forecast()), convert EUR/MWh -> CHF/kWh,
    and persist for Sparkplug LEG/ExportPrice
    (shareomat.sparkplug.coordinator_mapping) to publish unchanged.

    Emsomat's slot lookup (LegPriceForecast.slot_for(), "last known value
    at/before ts") already handles a coarser-than-requested resolution
    correctly without interpolating - so persisting at native resolution
    here is sufficient; no resampling is done or needed on this side.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from shareomat.database.day_ahead_prices import save_day_ahead_prices
from shareomat.external_data.market_forecast import (
    MarketForecastError,
    download_day_ahead_prices,
    download_exchange_rates,
)
from shareomat.models.external_data import DayAheadPricePoint

logger = logging.getLogger(__name__)


def fetch_and_persist_day_ahead_prices(db_path: Path, *, api_token: str, horizon_days: int = 7) -> int:
    """Fetch native-resolution day-ahead prices for the next `horizon_days`
    and persist them. Returns 0 (no error) if the token is missing/invalid
    or ENTSO-E/SNB are unreachable - graceful degradation, same convention
    as shareomat.adapters.priceProvider.PriceProviderError on the Emsomat
    side: caller falls back to whatever was already persisted, never crashes."""
    if not api_token:
        return 0

    now = datetime.now(timezone.utc)
    horizon_end = now + timedelta(days=horizon_days)

    try:
        prices = download_day_ahead_prices(now, horizon_end, api_token=api_token)
    except MarketForecastError as exc:
        logger.warning("ENTSO-E day-ahead price fetch failed: %s", exc)
        return 0
    if not prices:
        return 0

    try:
        rates = download_exchange_rates(now.strftime("%Y-%m"), horizon_end.strftime("%Y-%m"))
    except MarketForecastError as exc:
        logger.warning("SNB EUR/CHF rate fetch failed, using neutral 1.0 fallback: %s", exc)
        rates = []
    eur_chf_rate = (
        sum(r.rate_chf_per_eur for r in rates) / len(rates)
        if rates else Decimal("1.0")  # neutral fallback, same as calculate_pv_reference_price_forecast()
    )

    points = [
        DayAheadPricePoint(
            slot_start=p.start if p.start.tzinfo else p.start.replace(tzinfo=timezone.utc),
            price_chf_kwh=(p.price_eur_mwh * eur_chf_rate) / Decimal(1000),
        )
        for p in prices
    ]
    written = save_day_ahead_prices(db_path, points)
    logger.info("ENTSO-E day-ahead prices persisted: %d native-resolution point(s)", written)
    return written
