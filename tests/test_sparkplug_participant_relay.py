# -*- coding: utf-8 -*-
"""Tests for shareomat/sparkplug/participant_relay.py::ParticipantRelay."""
from __future__ import annotations

import pytest

from shareomat.config import MqttConfig, SparkplugConfig
from shareomat.sparkplug.participant_relay import ParticipantRelay
from shareomat.sparkplug.vendor.payload import DBirth, DData, DDeath, NBirth, NCmd
from shareomat.sparkplug.vendor.metric import Metric
from shareomat.sparkplug.vendor.datatype import DataType


class FakeClient:
    def __init__(self):
        self.will = None
        self.published = []
        self.subscribed = []
        self.on_connect = None
        self.on_message = None

    def will_set(self, topic, payload, qos=0, retain=False):
        self.will = (topic, payload, qos, retain)

    def username_pw_set(self, username, password=None):
        pass

    def tls_set(self, ca_certs=None):
        pass

    def reconnect_delay_set(self, min_delay=1, max_delay=30):
        pass

    def connect(self, host, port, keepalive=60):
        pass

    def loop_start(self):
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


class FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


def _sp_config(**overrides):
    defaults = dict(
        enabled=True, group_id="emsomat", primary_host_id="shareomat",
        emsomat_edge_node_id="EMSOMAT-A", participant_edge_node_id="shareomat-relay",
    )
    defaults.update(overrides)
    return SparkplugConfig(**defaults)


def _mqtt_config():
    return MqttConfig(enabled=True, broker="localhost", port=1883, client_id="shareomat")


def _relay(client=None):
    return ParticipantRelay(_sp_config(), _mqtt_config(), client=client or FakeClient())


def test_start_registers_ndeath_will_and_publishes_nbirth():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(), _mqtt_config(), client=client)
    assert relay.start() is True

    topic, payload, qos, retain = client.will
    assert topic == "spBv1.0/emsomat/NDEATH/shareomat-relay"
    assert qos == 0 and retain is False

    nbirths = [p for p in client.published if p[0] == "spBv1.0/emsomat/NBIRTH/shareomat-relay"]
    assert len(nbirths) == 1
    birth = NBirth.decode(nbirths[0][1])
    assert birth.seq == 0
    assert {m.name for m in birth.metrics} == {"bdSeq", "Node Control/Rebirth"}


def test_disabled_config_does_not_start():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(enabled=False), _mqtt_config(), client=client)
    assert relay.start() is False
    assert client.will is None


def test_register_participant_before_start_only_updates_registry():
    relay = _relay()
    relay.register_participant("PARTICIPANT-B", 1500.0, 0.0)
    assert relay.known_participants == ("PARTICIPANT-B",)


def test_register_participant_after_start_publishes_dbirth():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(), _mqtt_config(), client=client)
    relay.start()
    client.published.clear()

    relay.register_participant("PARTICIPANT-B", 1500.0, 0.0)

    dbirths = [p for p in client.published if p[0] == "spBv1.0/emsomat/DBIRTH/shareomat-relay/PARTICIPANT-B"]
    assert len(dbirths) == 1
    birth = DBirth.decode(dbirths[0][1])
    values = {m.name: m.value for m in birth.metrics}
    assert values["Market/ExportPowerNow"] == 1500.0
    assert values["Market/ImportPowerNow"] == 0.0


def test_registering_same_participant_twice_sends_ddata_not_second_dbirth():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(), _mqtt_config(), client=client)
    relay.start()
    relay.register_participant("PARTICIPANT-B", 1000.0, 0.0)
    client.published.clear()

    relay.register_participant("PARTICIPANT-B", 2000.0, 0.0)

    dbirths = [p for p in client.published if p[0].startswith("spBv1.0/emsomat/DBIRTH/")]
    ddatas = [p for p in client.published if p[0].startswith("spBv1.0/emsomat/DDATA/")]
    assert dbirths == []
    assert len(ddatas) == 1


def test_update_participant_publishes_ddata():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(), _mqtt_config(), client=client)
    relay.start()
    relay.register_participant("PARTICIPANT-B", 1000.0, 0.0)
    client.published.clear()

    relay.update_participant("PARTICIPANT-B", 2500.0, 100.0)

    ddatas = [p for p in client.published if p[0] == "spBv1.0/emsomat/DDATA/shareomat-relay/PARTICIPANT-B"]
    assert len(ddatas) == 1
    data = DData.decode(ddatas[0][1])
    values = {m.name: m.value for m in data.metrics}
    assert values["Market/ExportPowerNow"] == 2500.0
    assert values["Market/ImportPowerNow"] == 100.0


def test_update_unknown_participant_raises():
    relay = _relay()
    with pytest.raises(ValueError):
        relay.update_participant("GHOST", 1.0, 0.0)


def test_deregister_participant_publishes_ddeath_and_removes():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(), _mqtt_config(), client=client)
    relay.start()
    relay.register_participant("PARTICIPANT-B", 1000.0, 0.0)
    client.published.clear()

    relay.deregister_participant("PARTICIPANT-B")

    ddeaths = [p for p in client.published if p[0] == "spBv1.0/emsomat/DDEATH/shareomat-relay/PARTICIPANT-B"]
    assert len(ddeaths) == 1
    DDeath.decode(ddeaths[0][1])  # doesn't raise
    assert relay.known_participants == ()


def test_deregister_unknown_participant_is_noop():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(), _mqtt_config(), client=client)
    relay.start()
    client.published.clear()
    relay.deregister_participant("GHOST")
    assert client.published == []


def test_ncmd_rebirth_republishes_nbirth_and_all_participant_dbirths():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(), _mqtt_config(), client=client)
    relay.start()
    relay.register_participant("PARTICIPANT-B", 1000.0, 0.0)
    relay.register_participant("PARTICIPANT-C", 0.0, 500.0)
    client.published.clear()

    rebirth_metric = Metric(timestamp=1, name="Node Control/Rebirth", datatype=DataType.BOOLEAN, value=True)
    ncmd = NCmd(timestamp=1, metrics=(rebirth_metric,))
    relay._on_message(client, None, FakeMsg("spBv1.0/emsomat/NCMD/shareomat-relay", ncmd.encode(include_dtypes=True)))

    nbirths = [p for p in client.published if p[0] == "spBv1.0/emsomat/NBIRTH/shareomat-relay"]
    dbirths = {p[0] for p in client.published if p[0].startswith("spBv1.0/emsomat/DBIRTH/")}
    assert len(nbirths) == 1
    assert dbirths == {
        "spBv1.0/emsomat/DBIRTH/shareomat-relay/PARTICIPANT-B",
        "spBv1.0/emsomat/DBIRTH/shareomat-relay/PARTICIPANT-C",
    }


def test_bdseq_increments_on_each_start():
    client = FakeClient()
    relay = ParticipantRelay(_sp_config(), _mqtt_config(), client=client)
    relay.start()
    first_bdseq = relay._bdseq
    relay.stop()
    relay.start()
    assert relay._bdseq == first_bdseq + 1
