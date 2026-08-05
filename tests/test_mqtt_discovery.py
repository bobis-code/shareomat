# -*- coding: utf-8 -*-
"""Tests for shareomat.ha.mqtt_discovery HA MQTT Discovery payloads."""

from __future__ import annotations

import json

from shareomat.config import MqttConfig
from shareomat.ha.mqtt_discovery import publish_engine_discovery


class FakeClient:
    """Captures .publish() calls instead of talking to a real broker."""

    def __init__(self) -> None:
        self.published: dict[str, str] = {}

    def publish(self, topic, payload, qos=0, retain=False) -> None:
        self.published[topic] = payload


def test_engine_discovery_publishes_demand_test_number_entity() -> None:
    client = FakeClient()
    publish_engine_discovery(client, MqttConfig(topic_prefix="shareomat", discovery_prefix="homeassistant"))

    topic = "homeassistant/number/shareomat_engine_demand_test/config"
    assert topic in client.published

    payload = json.loads(client.published[topic])
    assert payload["command_topic"] == "shareomat/energy_data/demand_forecast/test_set"
    assert payload["unit_of_measurement"] == "kWh"
    assert payload["optimistic"] is True
    assert payload["device"]["identifiers"] == ["shareomat_engine"]
