# -*- coding: utf-8 -*-
"""Tests for persisted external-data results (exchange_rates, elcom_tariffs)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from shareomat.database.elcom_tariffs import list_elcom_tariffs, save_elcom_tariffs
from shareomat.database.exchange_rates import list_exchange_rates, save_exchange_rates
from shareomat.database.sqlite import init_db
from shareomat.external_data.elcom import ElcomTariffComponents
from shareomat.external_data.market_forecast import ExchangeRate


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    return path


def test_exchange_rates_roundtrip(db_path):
    rates = [
        ExchangeRate(period="2026-01", rate_chf_per_eur=Decimal("0.92732")),
        ExchangeRate(period="2026-02", rate_chf_per_eur=Decimal("0.91406")),
    ]
    save_exchange_rates(db_path, "EUR", rates)
    stored = list_exchange_rates(db_path, currency="EUR")
    assert len(stored) == 2
    assert stored[0].period == "2026-02"  # newest period first
    assert stored[0].rate_chf_per_eur == Decimal("0.91406")


def test_exchange_rates_refetch_updates_in_place(db_path):
    save_exchange_rates(db_path, "EUR", [ExchangeRate(period="2026-01", rate_chf_per_eur=Decimal("0.9"))])
    save_exchange_rates(db_path, "EUR", [ExchangeRate(period="2026-01", rate_chf_per_eur=Decimal("0.95"))])
    stored = list_exchange_rates(db_path, currency="EUR")
    assert len(stored) == 1  # upsert, not a duplicate row
    assert stored[0].rate_chf_per_eur == Decimal("0.95")


def test_exchange_rates_scoped_by_currency(db_path):
    save_exchange_rates(db_path, "EUR", [ExchangeRate(period="2026-01", rate_chf_per_eur=Decimal("0.9"))])
    save_exchange_rates(db_path, "USD", [ExchangeRate(period="2026-01", rate_chf_per_eur=Decimal("0.8"))])
    assert len(list_exchange_rates(db_path, currency="EUR")) == 1
    assert len(list_exchange_rates(db_path, currency="USD")) == 1


def _tariff(category: str, energy: str) -> ElcomTariffComponents:
    return ElcomTariffComponents(
        category=category,
        energy_chf_kwh=Decimal(energy),
        grid_chf_kwh=Decimal("0.14"),
        aidfee_chf_kwh=Decimal("0.023"),
        community_fees_chf_kwh=Decimal("0.021"),
        total_chf_kwh=Decimal("0.30"),
        fixcosts_chf_year=Decimal("104"),
        operator_iri="https://example.test/operator/2",
        operator_name="AGE SA",
    )


def test_elcom_tariffs_roundtrip(db_path):
    save_elcom_tariffs(db_path, "5250", 2024, [_tariff("H4", "0.13456"), _tariff("C1", "0.14069")])
    stored = list_elcom_tariffs(db_path, municipality_bfs_number="5250")
    assert len(stored) == 2
    categories = {t.category for _, _, t in stored}
    assert categories == {"H4", "C1"}
    h4 = next(t for _, _, t in stored if t.category == "H4")
    assert h4.energy_chf_kwh == Decimal("0.13456")
    assert h4.operator_name == "AGE SA"


def test_elcom_tariffs_refetch_updates_in_place(db_path):
    save_elcom_tariffs(db_path, "5250", 2024, [_tariff("H4", "0.10")])
    save_elcom_tariffs(db_path, "5250", 2024, [_tariff("H4", "0.20")])
    stored = list_elcom_tariffs(db_path, municipality_bfs_number="5250")
    assert len(stored) == 1
    assert stored[0][2].energy_chf_kwh == Decimal("0.20")


def test_elcom_tariffs_scoped_by_municipality(db_path):
    save_elcom_tariffs(db_path, "5250", 2024, [_tariff("H4", "0.10")])
    save_elcom_tariffs(db_path, "261", 2024, [_tariff("H4", "0.12")])
    assert len(list_elcom_tariffs(db_path, municipality_bfs_number="5250")) == 1
    assert len(list_elcom_tariffs(db_path)) == 2  # no filter -> all municipalities
