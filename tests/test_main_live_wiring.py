# -*- coding: utf-8 -*-
"""Wiring of the live layer in main.py: optional, and it never blocks the rest."""
from __future__ import annotations

import json

import main as app
from shareomat.config import MqttConfig, RuntimeConfig, SparkplugConfig, WanConfig
from shareomat.database.sqlite import init_db
from shareomat.sparkplug.wan_downlink import WanDownlink


class FakeClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def _runtime(**overrides):
    runtime = RuntimeConfig.__new__(RuntimeConfig)
    runtime.mqtt = MqttConfig(enabled=True, broker="b", port=1883, client_id="c")
    runtime.sparkplug = overrides.get("sparkplug", SparkplugConfig(enabled=False))
    runtime.wan = overrides.get("wan", WanConfig(enabled=False))
    return runtime


def test_setup_live_publishes_discovery_and_runs(tmp_path):
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    client = FakeClient()
    service = app._setup_live(db_path, _runtime(), client)
    try:
        assert service is not None
        topics = [t for t, _, _ in client.published]
        assert "homeassistant/sensor/shareomat_live_netzeinspeisung/config" in topics
        payload = dict((t, p) for t, p, _ in client.published)["homeassistant/sensor/shareomat_leg_ueberschuss/config"]
        assert json.loads(payload)["device"]["identifiers"] == ["shareomat_live"]
    finally:
        service.stop()


def test_live_failure_does_not_stop_the_application(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(app, "LiveStatusService", boom)
    assert app._setup_live(tmp_path / "x.db", _runtime(), None) is None


def test_sparkplug_disabled_still_works_with_live_layer(tmp_path):
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    service = app._setup_live(db_path, _runtime(), None)
    try:
        assert app._setup_sparkplug(_runtime(), service) == (None, None, None, None)
    finally:
        service.stop()


def test_without_live_layer_wan_accepts_nothing(monkeypatch):
    captured = {}

    class FakeRelay:
        def __init__(self, *a, **k):
            pass

        def start(self):
            return True

    class FakeHost(FakeRelay):
        request_rebirth = None

    class FakeDownlink:
        def __init__(self, *a, **kwargs):
            captured.update(kwargs)

        def start(self):
            return False

    monkeypatch.setattr(app, "ParticipantRelay", FakeRelay)
    monkeypatch.setattr(app, "SparkplugHost", FakeHost)
    monkeypatch.setattr(app, "build_wan_uplink", lambda *a, **k: FakeRelay())
    monkeypatch.setattr(app, "WanDownlink", FakeDownlink)
    runtime = _runtime(sparkplug=SparkplugConfig(enabled=True, emsomat_edge_node_id="EMS"), wan=WanConfig(enabled=True))
    app._setup_sparkplug(runtime, None)
    assert captured["allowed_source"]("ANY", "ANY") is False
    assert WanDownlink is not FakeDownlink
