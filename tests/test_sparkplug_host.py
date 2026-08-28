# -*- coding: utf-8 -*-
"""Tests for shareomat/sparkplug/host.py::SparkplugHost."""
from __future__ import annotations

import pytest

from shareomat.config import MqttConfig, SparkplugConfig
from shareomat.sparkplug.host import SparkplugHost
from shareomat.sparkplug.vendor.datatype import DataType
from shareomat.sparkplug.vendor.metric import Metric
from shareomat.sparkplug.vendor.payload import NBirth, NCmd, NData, NDeath, State


class FakeClient:
    """Minimal paho-mqtt-client stand-in - no real broker."""

    def __init__(self):
        self.will = None
        self.published = []  # (topic, payload, qos, retain)
        self.subscribed = []
        self.on_connect = None
        self.on_message = None
        self.connected_to = None

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def username_pw_set(self, username, password=None):
        pass

    def tls_set(self, ca_certs=None):
        pass

    def reconnect_delay_set(self, min_delay=1, max_delay=30):
        pass

    def connect(self, host, port, keepalive=60):
        self.connected_to = (host, port)

    def loop_start(self):
        # simulate the broker accepting the connection immediately
        if self.on_connect:
            self.on_connect(self, None, {}, 0)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))


def _sp_config(**overrides):
    defaults = dict(enabled=True, group_id="emsomat", primary_host_id="shareomat", emsomat_edge_node_id="EMSOMAT-A")
    defaults.update(overrides)
    return SparkplugConfig(**defaults)


def _mqtt_config():
    return MqttConfig(enabled=True, broker="localhost", port=1883, client_id="shareomat")


class FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


def test_start_registers_state_will_and_connects():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    assert host.start() is True

    topic, payload, qos, retain = client.will
    assert topic == "spBv1.0/STATE/shareomat"
    assert payload == b"OFFLINE"
    assert qos == 1 and retain is True
    assert client.connected_to == ("localhost", 1883)


def test_on_connect_publishes_state_online_and_subscribes():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()

    online_publishes = [p for p in client.published if p[0] == "spBv1.0/STATE/shareomat"]
    assert len(online_publishes) == 1
    topic, payload, qos, retain = online_publishes[0]
    assert payload == b"ONLINE"
    assert qos == 1 and retain is True

    subscribed_topics = {t for t, _ in client.subscribed}
    assert "spBv1.0/emsomat/NBIRTH/EMSOMAT-A" in subscribed_topics
    assert "spBv1.0/emsomat/NDATA/EMSOMAT-A" in subscribed_topics
    assert "spBv1.0/emsomat/NDEATH/EMSOMAT-A" in subscribed_topics
    assert all(qos == 0 for _, qos in client.subscribed)


def test_disabled_config_does_not_start():
    client = FakeClient()
    host = SparkplugHost(_sp_config(enabled=False), _mqtt_config(), client=client)
    assert host.start() is False
    assert client.will is None


def test_stop_publishes_offline_gracefully():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()
    client.published.clear()

    host.stop()
    offline_publishes = [p for p in client.published if p[0] == "spBv1.0/STATE/shareomat" and p[1] == b"OFFLINE"]
    assert len(offline_publishes) == 1


# ---------------------------------------------------------------------------
# Emsomat lifecycle tracking
# ---------------------------------------------------------------------------

def _nbirth_bytes(export_w=1000.0, import_w=0.0, bdseq=1):
    metrics = (
        Metric(timestamp=1, name="bdSeq", datatype=DataType.INT64, value=bdseq),
        Metric(timestamp=1, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=export_w),
        Metric(timestamp=1, name="Market/ImportPowerNow", datatype=DataType.DOUBLE, value=import_w),
    )
    return NBirth(timestamp=1, seq=0, metrics=metrics).encode(include_dtypes=True)


def test_nbirth_marks_emsomat_online_and_stores_metrics():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()

    host._on_message(client, None, FakeMsg("spBv1.0/emsomat/NBIRTH/EMSOMAT-A", _nbirth_bytes(2500.0, 0.0)))

    assert host.emsomat_online is True
    assert host.get_metric_value("Market/ExportPowerNow") == 2500.0
    assert host.get_metric_value("bdSeq") == 1


def test_ndata_updates_metrics_using_birth_for_alias_resolution():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()
    host._on_message(client, None, FakeMsg("spBv1.0/emsomat/NBIRTH/EMSOMAT-A", _nbirth_bytes(1000.0, 0.0)))

    ndata_metrics = (
        Metric(timestamp=2, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=3000.0),
        Metric(timestamp=2, name="Market/ImportPowerNow", datatype=DataType.DOUBLE, value=0.0),
    )
    ndata = NData(timestamp=2, seq=1, metrics=ndata_metrics)
    host._on_message(client, None, FakeMsg("spBv1.0/emsomat/NDATA/EMSOMAT-A", ndata.encode(include_dtypes=True)))

    assert host.get_metric_value("Market/ExportPowerNow") == 3000.0


def test_ndeath_with_matching_bdseq_marks_offline():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()
    host._on_message(client, None, FakeMsg("spBv1.0/emsomat/NBIRTH/EMSOMAT-A", _nbirth_bytes(bdseq=5)))
    assert host.emsomat_online is True

    bd_metric = Metric(timestamp=3, name="bdSeq", datatype=DataType.INT64, value=5)
    ndeath = NDeath(timestamp=None, bd_seq_metric=bd_metric)
    host._on_message(client, None, FakeMsg("spBv1.0/emsomat/NDEATH/EMSOMAT-A", ndeath.encode()))

    assert host.emsomat_online is False


def test_stale_ndeath_with_old_bdseq_is_ignored():
    """bdSeq-Korrelation (Abschnitt 28.4): eine verspaetet eintreffende NDEATH
    aus einer alten Session (altes bdSeq) darf die aktuelle Session nicht
    faelschlich als offline markieren."""
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()
    host._on_message(client, None, FakeMsg("spBv1.0/emsomat/NBIRTH/EMSOMAT-A", _nbirth_bytes(bdseq=5)))
    assert host.emsomat_online is True

    stale_bd_metric = Metric(timestamp=3, name="bdSeq", datatype=DataType.INT64, value=4)  # old session
    stale_ndeath = NDeath(timestamp=None, bd_seq_metric=stale_bd_metric)
    host._on_message(client, None, FakeMsg("spBv1.0/emsomat/NDEATH/EMSOMAT-A", stale_ndeath.encode()))

    assert host.emsomat_online is True  # unchanged - stale death ignored


def test_request_rebirth_publishes_ncmd():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()
    client.published.clear()

    host.request_rebirth()

    ncmd_publishes = [p for p in client.published if p[0] == "spBv1.0/emsomat/NCMD/EMSOMAT-A"]
    assert len(ncmd_publishes) == 1
    decoded = NCmd.decode(ncmd_publishes[0][1])
    rebirth_metric = next(m for m in decoded.metrics if m.name == "Node Control/Rebirth")
    assert rebirth_metric.value is True


def test_request_rebirth_before_connected_is_noop():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.request_rebirth()  # never started/connected
    assert client.published == []


def test_get_metric_value_returns_none_for_unknown_metric():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()
    assert host.get_metric_value("does/not/exist") is None


# ---------------------------------------------------------------------------
# publish_coordinator_ncmd() - LEG/DemandForecast + LEG/ExportPrice + LEG/FeedInPrice
# ---------------------------------------------------------------------------

def test_publish_coordinator_ncmd_sends_combined_metrics():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.start()
    client.published.clear()

    metrics = (
        Metric(timestamp=1, name="LEG/DemandForecast/Quality", datatype=DataType.STRING, value="ok"),
        Metric(timestamp=1, name="LEG/ExportPrice/Quality", datatype=DataType.STRING, value="ok"),
    )
    host.publish_coordinator_ncmd(metrics)

    ncmd_publishes = [p for p in client.published if p[0] == "spBv1.0/emsomat/NCMD/EMSOMAT-A"]
    assert len(ncmd_publishes) == 1
    decoded = NCmd.decode(ncmd_publishes[0][1])
    names = {m.name for m in decoded.metrics}
    assert "LEG/DemandForecast/Quality" in names
    assert "LEG/ExportPrice/Quality" in names


def test_publish_coordinator_ncmd_before_connected_is_noop():
    client = FakeClient()
    host = SparkplugHost(_sp_config(), _mqtt_config(), client=client)
    host.publish_coordinator_ncmd((Metric(timestamp=1, name="LEG/DemandForecast/Quality", datatype=DataType.STRING, value="ok"),))
    assert client.published == []
