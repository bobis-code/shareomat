# -*- coding: utf-8 -*-
"""Tests for the external-data database layer (supplier_tariffs, reference_prices, price_forecasts, data_imports, external_settings)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from shareomat.database.data_imports import list_recent_imports, record_import
from shareomat.database.external_settings import get_external_data_settings, save_external_data_settings
from shareomat.database.price_forecasts import list_price_forecasts, save_price_forecast
from shareomat.database.reference_prices import (
    get_reference_price_for_period,
    list_reference_prices,
    save_reference_price,
)
from shareomat.database.sqlite import init_db
from shareomat.database.supplier_tariffs import create_supplier_tariff, list_supplier_tariffs
from shareomat.models.external_data import (
    DataImportRecord,
    ExternalDataSettings,
    PriceForecast,
    ReferencePrice,
    SupplierTariff,
)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    return path


def test_supplier_tariff_roundtrip_preserves_decimal(db_path):
    create_supplier_tariff(db_path, SupplierTariff(
        source="EBL", valid_from=date(2027, 1, 1),
        energy_rate_ht_chf_kwh=Decimal("0.15"), grid_rate_ht_chf_kwh=Decimal("0.10"),
    ))
    tariffs = list_supplier_tariffs(db_path)
    assert len(tariffs) == 1
    assert tariffs[0].energy_rate_ht_chf_kwh == Decimal("0.15")
    assert tariffs[0].source == "EBL"


def test_reference_price_upsert_by_technology_period_source(db_path):
    price = ReferencePrice(
        technology="pv", period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
        price_chf_kwh=Decimal("0.08"), source="BFE",
    )
    save_reference_price(db_path, price)
    save_reference_price(db_path, ReferencePrice(
        technology="pv", period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
        price_chf_kwh=Decimal("0.09"), source="BFE",
    ))
    prices = list_reference_prices(db_path, technology="pv")
    assert len(prices) == 1  # same (technology, period_start, source) -> updated, not duplicated
    assert prices[0].price_chf_kwh == Decimal("0.09")


def test_get_reference_price_for_period_matches_covering_range(db_path):
    save_reference_price(db_path, ReferencePrice(
        technology="pv", period_start=date(2026, 1, 1), period_end=date(2026, 3, 31),
        price_chf_kwh=Decimal("0.08"), source="BFE",
    ))
    found = get_reference_price_for_period(db_path, "pv", date(2026, 2, 15))
    assert found is not None
    assert found.price_chf_kwh == Decimal("0.08")
    assert get_reference_price_for_period(db_path, "pv", date(2027, 1, 1)) is None


def test_price_forecast_roundtrip(db_path):
    save_price_forecast(db_path, PriceForecast(
        period_start=date(2026, 7, 1), period_end=date(2026, 7, 31),
        forecast_price_chf_kwh=Decimal("0.075"), sources=["entsoe", "snb"],
        completeness_pct=Decimal("95.5"), computed_at=datetime.now(timezone.utc),
    ))
    forecasts = list_price_forecasts(db_path)
    assert len(forecasts) == 1
    assert forecasts[0].sources == ["entsoe", "snb"]
    assert forecasts[0].forecast_price_chf_kwh == Decimal("0.075")


def test_data_import_log_is_append_only_and_ordered(db_path):
    record_import(db_path, DataImportRecord(source="ElCom", data_type="tariffs", status="ok"))
    record_import(db_path, DataImportRecord(source="SNB", data_type="exchange_rates", status="error", detail="timeout"))
    imports = list_recent_imports(db_path)
    assert len(imports) == 2
    assert imports[0].source == "SNB"  # newest first
    assert imports[0].detail == "timeout"


def test_external_data_settings_default_empty_token(db_path):
    settings = get_external_data_settings(db_path)
    assert settings.entsoe_api_token == ""


def test_external_data_settings_roundtrip(db_path):
    save_external_data_settings(db_path, ExternalDataSettings(entsoe_api_token="secret-token-123"))
    assert get_external_data_settings(db_path).entsoe_api_token == "secret-token-123"
