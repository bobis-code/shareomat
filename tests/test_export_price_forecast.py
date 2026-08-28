# -*- coding: utf-8 -*-
"""Tests for shareomat/core/pipeline/export_price_forecast.py - native-resolution
ENTSO-E day-ahead price fetch/persist (see Emsomat_Shareomat_MQTT_Vertrag.md
Abschnitt 30, LEG-Exportpreis-Granularitaets-Korrektur)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

import shareomat.core.pipeline.export_price_forecast as export_price_forecast
from shareomat.core.pipeline.export_price_forecast import fetch_and_persist_day_ahead_prices
from shareomat.database.day_ahead_prices import list_day_ahead_prices
from shareomat.database.sqlite import init_db
from shareomat.external_data.market_forecast import DayAheadPrice, ExchangeRate, MarketForecastError


def test_no_token_skips_fetch_and_returns_zero(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)

    called = []
    monkeypatch.setattr(export_price_forecast, "download_day_ahead_prices", lambda *a, **k: called.append(1) or [])

    written = fetch_and_persist_day_ahead_prices(db_path, api_token="")

    assert written == 0
    assert called == []  # never even attempted the fetch without a token


def test_native_hourly_resolution_is_preserved_not_aggregated(tmp_path, monkeypatch) -> None:
    """Regression guard: two distinct hourly ENTSO-E points must persist as
    two distinct slots, never averaged into a single daily value."""
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)

    hourly_prices = [
        DayAheadPrice(start=datetime(2026, 8, 28, 12, 0), price_eur_mwh=Decimal("100.0")),
        DayAheadPrice(start=datetime(2026, 8, 28, 13, 0), price_eur_mwh=Decimal("200.0")),
    ]
    monkeypatch.setattr(export_price_forecast, "download_day_ahead_prices", lambda *a, **k: hourly_prices)
    monkeypatch.setattr(export_price_forecast, "download_exchange_rates", lambda *a, **k: [ExchangeRate(period="2026-08", rate_chf_per_eur=Decimal("1.0"))])

    written = fetch_and_persist_day_ahead_prices(db_path, api_token="dummy-token")

    assert written == 2
    points = list_day_ahead_prices(db_path)
    assert len(points) == 2  # NICHT zu einem einzigen Tageswert verschmolzen
    assert points[0].slot_start == datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    assert points[0].price_chf_kwh == pytest.approx(Decimal("0.1"))   # 100 EUR/MWh * 1.0 / 1000
    assert points[1].price_chf_kwh == pytest.approx(Decimal("0.2"))   # 200 EUR/MWh * 1.0 / 1000


def test_snb_failure_falls_back_to_neutral_rate(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)

    monkeypatch.setattr(export_price_forecast, "download_day_ahead_prices", lambda *a, **k: [
        DayAheadPrice(start=datetime(2026, 8, 28, 12, 0), price_eur_mwh=Decimal("100.0")),
    ])

    def _raise(*a, **k):
        raise MarketForecastError("SNB unreachable")
    monkeypatch.setattr(export_price_forecast, "download_exchange_rates", _raise)

    written = fetch_and_persist_day_ahead_prices(db_path, api_token="dummy-token")

    assert written == 1
    points = list_day_ahead_prices(db_path)
    assert points[0].price_chf_kwh == pytest.approx(Decimal("0.1"))  # neutral 1.0 fallback rate


def test_entsoe_failure_returns_zero_without_crashing(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)

    def _raise(*a, **k):
        raise MarketForecastError("ENTSO-E unreachable")
    monkeypatch.setattr(export_price_forecast, "download_day_ahead_prices", _raise)

    written = fetch_and_persist_day_ahead_prices(db_path, api_token="dummy-token")

    assert written == 0
    assert list_day_ahead_prices(db_path) == []


def test_refetch_upserts_by_slot_not_accumulating_duplicates(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)

    monkeypatch.setattr(export_price_forecast, "download_exchange_rates", lambda *a, **k: [])

    monkeypatch.setattr(export_price_forecast, "download_day_ahead_prices", lambda *a, **k: [
        DayAheadPrice(start=datetime(2026, 8, 28, 12, 0), price_eur_mwh=Decimal("100.0")),
    ])
    fetch_and_persist_day_ahead_prices(db_path, api_token="dummy-token")

    monkeypatch.setattr(export_price_forecast, "download_day_ahead_prices", lambda *a, **k: [
        DayAheadPrice(start=datetime(2026, 8, 28, 12, 0), price_eur_mwh=Decimal("150.0")),
    ])
    fetch_and_persist_day_ahead_prices(db_path, api_token="dummy-token")

    points = list_day_ahead_prices(db_path)
    assert len(points) == 1
    assert points[0].price_chf_kwh == pytest.approx(Decimal("0.15"))
