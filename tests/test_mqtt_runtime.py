# -*- coding: utf-8 -*-
"""Tests for shareomat.ha.mqtt_runtime.publish_energy_data_snapshot."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from shareomat.config import MqttConfig
from shareomat.database.consumption_forecasts import save_consumption_forecast
from shareomat.database.price_forecasts import save_price_forecast
from shareomat.database.settlement_history import save_settlement_snapshot
from shareomat.database.sqlite import init_db
from shareomat.ha.mqtt_runtime import (
    publish_demand_forecast,
    publish_energy_data_snapshot,
    publish_manual_demand_test,
    setup_command_subscription,
)
from shareomat.models.billing import BillingRecord
from shareomat.models.external_data import PriceForecast
from shareomat.models.forecast import ConsumptionForecastPoint


class FakeClient:
    """Captures .publish() calls instead of talking to a real broker."""

    def __init__(self) -> None:
        self.published: dict[str, str] = {}
        self._userdata: dict = {}
        self.subscribed: list[tuple[str, int]] = []
        self.on_message = None

    def publish(self, topic, payload, qos=0, retain=False) -> None:
        self.published[topic] = payload

    def user_data_get(self):
        return self._userdata

    def user_data_set(self, data) -> None:
        self._userdata = data

    def subscribe(self, topic, qos=0) -> None:
        self.subscribed.append((topic, qos))


class FakeMessage:
    """Minimal stand-in for a paho MQTTMessage, for feeding on_message directly."""

    def __init__(self, topic: str, payload: str, retain: bool = False) -> None:
        self.topic = topic
        self.payload = payload.encode("utf-8")
        self.retain = retain


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    return path


def _seed_price_forecast(db_path, days_from_today=0):
    start = date.today() + timedelta(days=days_from_today)
    save_price_forecast(db_path, PriceForecast(
        period_start=start, period_end=start,
        forecast_price_chf_kwh=Decimal("0.25"), sources=["entsoe"],
        computed_at=datetime.now(timezone.utc),
    ))


def _seed_settlement_history(db_path):
    now = datetime.now(timezone.utc)
    start = now - timedelta(hours=6)
    record = BillingRecord(
        participant_id="cons_a", label="Consumer A", meter_ids=["M1"],
        period_start=start, period_end=now,
        total_export_kwh=0.0, local_supplied_kwh=0.0, grid_export_kwh=0.0,
        total_import_kwh=10.0, local_received_kwh=7.0, grid_import_kwh=3.0,
        local_rate_chf=0.2, grid_rate_chf=0.3,
        local_cost_chf=1.4, grid_cost_chf=0.9, total_cost_chf=2.3,
    )
    save_settlement_snapshot(db_path, uuid.uuid4().hex, "fp-mqtt-test", start, now, [record])


def test_publishes_both_topics_retained(db_path) -> None:
    client = FakeClient()
    config = MqttConfig(topic_prefix="shareomat")
    publish_energy_data_snapshot(client, config, db_path)

    assert "shareomat/energy_data/prices" in client.published
    assert "shareomat/energy_data/local_grid" in client.published


def test_envelope_has_required_metadata_fields(db_path) -> None:
    client = FakeClient()
    config = MqttConfig(topic_prefix="shareomat", energy_data_ttl_seconds=3600)
    publish_energy_data_snapshot(client, config, db_path)

    payload = json.loads(client.published["shareomat/energy_data/prices"])
    for key in ("schema_version", "created_at", "valid_until", "source", "quality", "data"):
        assert key in payload

    created_at = datetime.fromisoformat(payload["created_at"])
    valid_until = datetime.fromisoformat(payload["valid_until"])
    assert (valid_until - created_at) == timedelta(seconds=3600)


def test_price_series_marked_as_forecast(db_path) -> None:
    _seed_price_forecast(db_path, days_from_today=1)
    client = FakeClient()
    publish_energy_data_snapshot(client, MqttConfig(topic_prefix="shareomat"), db_path)

    payload = json.loads(client.published["shareomat/energy_data/prices"])
    series = payload["data"]["series"]
    assert len(series) == 1
    assert series[0]["kind"] == "forecast"
    assert series[0]["price_chf_kwh"] == "0.25"


def test_price_series_excludes_out_of_horizon_forecasts(db_path) -> None:
    _seed_price_forecast(db_path, days_from_today=30)  # far beyond the 7-day horizon
    client = FakeClient()
    publish_energy_data_snapshot(client, MqttConfig(topic_prefix="shareomat"), db_path)

    payload = json.loads(client.published["shareomat/energy_data/prices"])
    assert payload["data"]["series"] == []


def test_local_grid_marked_as_recent_actual_not_forecast(db_path) -> None:
    _seed_settlement_history(db_path)
    client = FakeClient()
    publish_energy_data_snapshot(client, MqttConfig(topic_prefix="shareomat"), db_path)

    payload = json.loads(client.published["shareomat/energy_data/local_grid"])
    assert payload["data"]["leg"]["series"][0]["kind"] == "recent_actual"
    assert "cons_a" in payload["data"]["participants"]
    assert payload["data"]["participants"]["cons_a"][0]["kind"] == "recent_actual"


def test_no_data_still_publishes_valid_empty_envelope(db_path) -> None:
    client = FakeClient()
    publish_energy_data_snapshot(client, MqttConfig(topic_prefix="shareomat"), db_path)

    prices = json.loads(client.published["shareomat/energy_data/prices"])
    local_grid = json.loads(client.published["shareomat/energy_data/local_grid"])
    assert prices["data"]["series"] == []
    assert local_grid["data"]["leg"]["series"] == []
    assert local_grid["data"]["participants"] == {}


# ── publish_demand_forecast ─────────────────────────────────────────────────


def _forecast_point(slot_start, forecast_kwh=1.5, quality="ok") -> ConsumptionForecastPoint:
    return ConsumptionForecastPoint(
        scope="leg", participant_id="", slot_start=slot_start,
        forecast_kwh=forecast_kwh, quality=quality, method="weekday_time_weighted_recency_v1",
        sample_count=5, data_period_start=slot_start - timedelta(weeks=8), data_period_end=slot_start,
    )


def test_demand_forecast_published_on_its_own_topic(db_path) -> None:
    now = datetime.now(timezone.utc)
    save_consumption_forecast(db_path, [_forecast_point(now)])
    client = FakeClient()
    publish_demand_forecast(client, MqttConfig(topic_prefix="shareomat"), db_path)

    assert "shareomat/energy_data/demand_forecast" in client.published
    assert "shareomat/energy_data/local_grid" not in client.published


def test_demand_forecast_series_marked_as_forecast_not_recent_actual(db_path) -> None:
    now = datetime.now(timezone.utc) + timedelta(minutes=1)  # safely after publish_demand_forecast's own "now"
    save_consumption_forecast(db_path, [_forecast_point(now)])
    client = FakeClient()
    publish_demand_forecast(client, MqttConfig(topic_prefix="shareomat"), db_path)

    payload = json.loads(client.published["shareomat/energy_data/demand_forecast"])
    assert payload["data"]["series"][0]["kind"] == "forecast"
    assert payload["data"]["method"] == "weekday_time_weighted_recency_v1"
    assert payload["data"]["scope"] == "leg"


def test_demand_forecast_overall_quality_ok_when_near_term_mostly_ok(db_path) -> None:
    base = datetime.now(timezone.utc) + timedelta(minutes=1)
    save_consumption_forecast(db_path, [
        _forecast_point(base + timedelta(minutes=15 * i), quality="ok") for i in range(4)
    ])
    client = FakeClient()
    publish_demand_forecast(client, MqttConfig(topic_prefix="shareomat"), db_path)

    payload = json.loads(client.published["shareomat/energy_data/demand_forecast"])
    assert payload["quality"] == "ok"


def test_demand_forecast_overall_quality_insufficient_when_no_data(db_path) -> None:
    client = FakeClient()
    publish_demand_forecast(client, MqttConfig(topic_prefix="shareomat"), db_path)

    payload = json.loads(client.published["shareomat/energy_data/demand_forecast"])
    assert payload["quality"] == "insufficient_data"
    assert payload["data"]["series"] == []


# ── publish_manual_demand_test / demand_forecast test_set command ──────────


def test_manual_demand_test_publishes_onto_the_real_demand_forecast_topic() -> None:
    client = FakeClient()
    publish_manual_demand_test(client, MqttConfig(topic_prefix="shareomat"), 3.5)

    payload = json.loads(client.published["shareomat/energy_data/demand_forecast"])
    assert payload["data"]["method"] == "manual_test_override"
    series = payload["data"]["series"]
    assert len(series) == 1
    assert series[0]["forecast_kwh"] == 3.5
    assert series[0]["quality"] == "ok"
    assert series[0]["kind"] == "forecast"


def test_command_subscription_subscribes_to_demand_test_topic() -> None:
    client = FakeClient()
    setup_command_subscription(client, on_run=lambda: None, config=MqttConfig(topic_prefix="shareomat"))

    assert ("shareomat/energy_data/demand_forecast/test_set", 1) in client.subscribed


def test_demand_test_command_publishes_manual_value() -> None:
    client = FakeClient()
    setup_command_subscription(client, on_run=lambda: None, config=MqttConfig(topic_prefix="shareomat"))

    client.on_message(client, None, FakeMessage("shareomat/energy_data/demand_forecast/test_set", "7.25"))

    payload = json.loads(client.published["shareomat/energy_data/demand_forecast"])
    assert payload["data"]["series"][0]["forecast_kwh"] == 7.25
    assert payload["data"]["method"] == "manual_test_override"


def test_demand_test_command_ignores_retained_replay() -> None:
    client = FakeClient()
    setup_command_subscription(client, on_run=lambda: None, config=MqttConfig(topic_prefix="shareomat"))

    client.on_message(
        client, None,
        FakeMessage("shareomat/energy_data/demand_forecast/test_set", "7.25", retain=True),
    )

    assert "shareomat/energy_data/demand_forecast" not in client.published


def test_demand_test_command_ignores_non_numeric_payload() -> None:
    client = FakeClient()
    setup_command_subscription(client, on_run=lambda: None, config=MqttConfig(topic_prefix="shareomat"))

    client.on_message(client, None, FakeMessage("shareomat/energy_data/demand_forecast/test_set", "not-a-number"))

    assert "shareomat/energy_data/demand_forecast" not in client.published
