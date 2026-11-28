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


def _config() -> MqttConfig:
    return MqttConfig(topic_prefix="shareomat", discovery_prefix="homeassistant")


def test_engine_discovery_has_only_the_status_sensor() -> None:
    """B26/P2d: published at every start; the sensors of the CSV run are gone (0.6.34)."""
    import json
    client = FakeClient()
    publish_engine_discovery(client, _config())
    assert list(client.published) == ["homeassistant/sensor/shareomat_engine_status/config"]
    payload = json.loads(client.published["homeassistant/sensor/shareomat_engine_status/config"])
    assert payload["state_topic"] == "shareomat/status" and payload["unique_id"] == "shareomat_engine_status"


def test_entities_of_the_csv_run_are_removed_from_home_assistant() -> None:
    """An empty retained message deletes the Discovery config; the retained values are cleared as well."""
    from shareomat.ha.mqtt_discovery import retire_entities
    client = FakeClient()
    retire_entities(client, _config(), ["Haus 1", "P2"])
    assert set(client.published.values()) == {""}
    for domain, suffix in (("sensor", "last_run"), ("sensor", "inbox_count"), ("sensor", "report_count"),
                           ("sensor", "last_error"), ("button", "run_now"), ("switch", "auto_scan")):
        assert f"homeassistant/{domain}/shareomat_engine_{suffix}/config" in client.published
    for topic in ("last_run", "inbox_count", "report_count", "last_error", "auto_scan/state"):
        assert f"shareomat/{topic}" in client.published
    for pid in ("haus_1", "p2"):                                   # as the removed code made them topic-safe
        for field in ("total_cost_chf", "local_cost_chf", "grid_cost_chf", "local_share_kwh", "grid_import_kwh",
                      "total_import_kwh"):
            assert f"homeassistant/sensor/shareomat_{pid}_{field}/config" in client.published
            assert f"shareomat/billing/{pid}/{field}" in client.published
    assert "homeassistant/sensor/shareomat_engine_status/config" not in client.published   # the status stays
    assert "shareomat/status" not in client.published


def test_startup_discovery_never_stops_the_start(monkeypatch, caplog, tmp_path) -> None:
    import main as app
    from shareomat.ha import mqtt_discovery

    def broken(*args):
        raise RuntimeError("broker weg")

    monkeypatch.setattr(mqtt_discovery, "publish_engine_discovery", broken)
    runtime = type("R", (), {"mqtt": _config()})()
    with caplog.at_level("ERROR", logger="main"):
        app._publish_ha_discovery(FakeClient(), runtime, tmp_path / "x.db")
    assert "could not be published" in caplog.text
