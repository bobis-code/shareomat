# -*- coding: utf-8 -*-
"""Tests for shareomat.ha.mqtt_discovery HA MQTT Discovery payloads."""

from __future__ import annotations

from shareomat.config import MqttConfig
from shareomat.ha.mqtt_discovery import publish_engine_discovery


class FakeClient:
    """Captures .publish() calls instead of talking to a real broker."""

    def __init__(self) -> None:
        self.published: dict[str, str] = {}

    def publish(self, topic, payload, qos=0, retain=False) -> None:
        self.published[topic] = payload


def test_engine_discovery_publishes_no_number_entities_after_hard_cut() -> None:
    """The former 'demand_test' Number entity (published to the now-removed
    plain-MQTT energy_data/demand_forecast/test_set topic) was removed with
    the Sparkplug Hard Cut (see shareomat/ha/entities/numbers.py) - the
    NUMBERS loop in publish_engine_discovery() must handle an empty list
    gracefully, publishing no number/*/config topics at all."""
    client = FakeClient()
    publish_engine_discovery(client, MqttConfig(topic_prefix="shareomat", discovery_prefix="homeassistant"))

    assert not any(topic.startswith("homeassistant/number/") for topic in client.published)
