# -*- coding: utf-8 -*-
"""Tests for main.py's _publish_coordinator_sparkplug() - the Sparkplug NCMD
replacement for the removed plain-MQTT {prefix}/energy_data/* publishers."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import main as app_main
from shareomat.config import LegConfig, MqttConfig, PathConfig, ProcessingConfig
from shareomat.database.consumption_forecasts import save_consumption_forecast
from shareomat.database.day_ahead_prices import list_day_ahead_prices, save_day_ahead_prices
from shareomat.database.external_settings import save_external_data_settings
from shareomat.database.sqlite import init_db
from shareomat.models.community import Community
from shareomat.models.external_data import DayAheadPricePoint, ExternalDataSettings
from shareomat.models.forecast import ConsumptionForecastPoint
from shareomat.models.tariff import Tariff


class FakeSparkplugHost:
    def __init__(self):
        self.published_metrics = None

    def publish_coordinator_ncmd(self, metrics):
        self.published_metrics = metrics


def _minimal_config() -> LegConfig:
    return LegConfig(
        community=Community(community_id="TEST-001", name="Test Community"),
        participants=[], meters=[],
        tariff=Tariff(
            local_rate_chf_kwh=Decimal("0.12"), grid_rate_chf_kwh=Decimal("0.28"),
            feed_in_rate_chf_kwh=Decimal("0.08"),
        ),
        paths=PathConfig(
            inbox=Path("/tmp/inbox"), archive=Path("/tmp/archive"),
            reports=Path("/tmp/reports"), state=Path("/tmp/state"),
        ),
        processing=ProcessingConfig(),
        mqtt=MqttConfig(energy_data_ttl_seconds=21600),
    )


def test_publish_coordinator_sparkplug_sends_all_three_metric_groups(tmp_path) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    now = datetime.now(timezone.utc)
    save_consumption_forecast(db_path, [
        ConsumptionForecastPoint(
            scope="leg", participant_id="", slot_start=now, forecast_kwh=1.5, quality="ok",
            method="weekday_time_weighted_recency_v1", sample_count=5,
            data_period_start=None, data_period_end=None,
        )
    ])

    host = FakeSparkplugHost()
    app_main._publish_coordinator_sparkplug(host, _minimal_config(), db_path)

    assert host.published_metrics is not None
    names = {m.name for m in host.published_metrics}
    assert "LEG/DemandForecast" in names
    assert "LEG/ExportPrice" in names
    assert "LEG/FeedInPrice" in names


def test_publish_coordinator_sparkplug_never_uses_local_rate(tmp_path) -> None:
    """Regression guard: the published LEG/FeedInPrice series must come from
    feed_in_rate_chf_kwh, never local_rate_chf_kwh (consumer price)."""
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    config = _minimal_config()
    config.tariff.local_rate_chf_kwh = Decimal("99.0")  # deliberately absurd

    host = FakeSparkplugHost()
    app_main._publish_coordinator_sparkplug(host, config, db_path)

    by_name = {m.name: m for m in host.published_metrics}
    feed_in_dataset = by_name["LEG/FeedInPrice"].value
    assert all(row[1] != 99.0 for row in feed_in_dataset.rows)
    assert all(row[1] == 0.08 for row in feed_in_dataset.rows)


def test_publish_coordinator_sparkplug_uses_native_resolution_export_price(tmp_path) -> None:
    """LEG/ExportPrice muss die native (z.B. stuendliche) Aufloesung der
    persistierten Preispunkte 1:1 durchreichen, keine Tagesaggregation
    (siehe export_price_forecast.py)."""
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    now = datetime.now(timezone.utc)
    save_day_ahead_prices(db_path, [
        DayAheadPricePoint(slot_start=now, price_chf_kwh=Decimal("0.15")),
        DayAheadPricePoint(slot_start=now.replace(hour=(now.hour + 1) % 24), price_chf_kwh=Decimal("0.25")),
    ])

    host = FakeSparkplugHost()
    app_main._publish_coordinator_sparkplug(host, _minimal_config(), db_path)

    by_name = {m.name: m for m in host.published_metrics}
    export_dataset = by_name["LEG/ExportPrice"].value
    assert len(export_dataset.rows) == 2


# ── _maybe_recompute_export_price_forecast() - throttle, same pattern as
# _maybe_recompute_demand_forecast (see test_main_demand_forecast.py) ──


def test_recompute_export_price_skips_without_token(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    save_external_data_settings(db_path, ExternalDataSettings(entsoe_api_token=""))

    calls = []
    monkeypatch.setattr(app_main, "fetch_and_persist_day_ahead_prices", lambda *a, **k: calls.append(1))

    app_main._maybe_recompute_export_price_forecast(db_path)

    assert calls == []


def test_recompute_export_price_fetches_with_configured_token(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    save_external_data_settings(db_path, ExternalDataSettings(entsoe_api_token="real-token"))

    calls = []
    monkeypatch.setattr(app_main, "fetch_and_persist_day_ahead_prices", lambda db_path, api_token: calls.append(api_token))

    app_main._maybe_recompute_export_price_forecast(db_path)

    assert calls == ["real-token"]


def test_recompute_export_price_throttles_within_interval(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    save_external_data_settings(db_path, ExternalDataSettings(entsoe_api_token="real-token"))
    save_day_ahead_prices(db_path, [DayAheadPricePoint(slot_start=datetime.now(timezone.utc), price_chf_kwh=Decimal("0.1"))])

    calls = []
    monkeypatch.setattr(app_main, "fetch_and_persist_day_ahead_prices", lambda db_path, api_token: calls.append(1))

    app_main._maybe_recompute_export_price_forecast(db_path)  # fresh fetch just happened above

    assert calls == []
