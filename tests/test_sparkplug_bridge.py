# -*- coding: utf-8 -*-
"""Tests for shareomat/sparkplug/bridge.py::mirror_host_to_uplink()."""
from __future__ import annotations

from shareomat.config import MqttConfig, SparkplugConfig, WanConfig
from shareomat.sparkplug.bridge import mirror_host_to_uplink
from shareomat.sparkplug.host import SparkplugHost
from shareomat.sparkplug.wan_uplink import build_wan_uplink
from shareomat.sparkplug.vendor.metric import Metric
from shareomat.sparkplug.vendor.datatype import DataType
from shareomat.sparkplug.vendor.payload import NBirth, NData


OWN_PARTICIPANT_ID = "P1"


def _host() -> SparkplugHost:
    sp_config = SparkplugConfig(enabled=True, group_id="emsomat", primary_host_id="shareomat", emsomat_edge_node_id="EMSOMAT-A")
    mqtt_config = MqttConfig(enabled=True, broker="localhost", port=1883, client_id="shareomat")
    return SparkplugHost(sp_config, mqtt_config, client=object())  # client unused for these tests


def _uplink():
    wan_config = WanConfig(enabled=True, broker="mqtt.shareomat.ch", group_id="leg-0001", edge_node_id="SITE-A", own_participant_id=OWN_PARTICIPANT_ID)
    return build_wan_uplink(wan_config, client_id="site-a-uplink")


def _nbirth_bytes(export_w, import_w, ts_ms):
    metrics = (
        Metric(timestamp=ts_ms, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=export_w),
        Metric(timestamp=ts_ms, name="Market/ImportPowerNow", datatype=DataType.DOUBLE, value=import_w),
    )
    return NBirth(timestamp=ts_ms, seq=0, metrics=metrics).encode(include_dtypes=True)


def test_noop_before_emsomat_has_sent_anything():
    host = _host()
    uplink = _uplink()
    mirror_host_to_uplink(host, uplink, OWN_PARTICIPANT_ID)
    assert uplink.known_participants == ()


def test_mirrors_export_and_import_power_correctly():
    host = _host()
    host._handle_nbirth(_nbirth_bytes(2500.0, 0.0, 123456))
    uplink = _uplink()

    mirror_host_to_uplink(host, uplink, OWN_PARTICIPANT_ID)

    assert OWN_PARTICIPANT_ID in uplink.known_participants
    assert uplink._participants[OWN_PARTICIPANT_ID] == (2500.0, 0.0)


def test_preserves_original_metric_timestamp():
    host = _host()
    ORIGINAL_TS = 999888777
    host._handle_nbirth(_nbirth_bytes(1000.0, 0.0, ORIGINAL_TS))
    uplink_client_capture = []

    class CapturingClient:
        def publish(self, topic, payload, qos=0, retain=False):
            capturing_payload.append(payload)
        def will_set(self, *a, **k): pass
        def username_pw_set(self, *a, **k): pass
        def tls_set(self, *a, **k): pass
        def reconnect_delay_set(self, *a, **k): pass
        def connect(self, *a, **k): pass
        def loop_start(self):
            if self.on_connect:
                self.on_connect(self, None, {}, 0)
        def loop_stop(self): pass
        def disconnect(self): pass
        def subscribe(self, *a, **k): pass
        on_connect = None
        on_message = None

    capturing_payload = []
    wan_config = WanConfig(enabled=True, broker="mqtt.shareomat.ch", group_id="leg-0001", edge_node_id="SITE-A", own_participant_id=OWN_PARTICIPANT_ID)
    client = CapturingClient()
    uplink = build_wan_uplink(wan_config, client_id="site-a-uplink", client=client)
    uplink.start()

    mirror_host_to_uplink(host, uplink, OWN_PARTICIPANT_ID)

    from shareomat.sparkplug.vendor.payload import DBirth
    dbirth = DBirth.decode(capturing_payload[-1])
    export_metric = next(m for m in dbirth.metrics if m.name == "Market/ExportPowerNow")
    assert export_metric.timestamp == ORIGINAL_TS


def test_second_call_updates_instead_of_registering_again():
    host = _host()
    host._handle_nbirth(_nbirth_bytes(1000.0, 0.0, 1000))
    uplink = _uplink()

    mirror_host_to_uplink(host, uplink, OWN_PARTICIPANT_ID)
    host._handle_ndata(NData(timestamp=2000, seq=1, metrics=(
        Metric(timestamp=2000, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=3000.0),
        Metric(timestamp=2000, name="Market/ImportPowerNow", datatype=DataType.DOUBLE, value=0.0),
    )).encode(include_dtypes=True))
    mirror_host_to_uplink(host, uplink, OWN_PARTICIPANT_ID)

    assert uplink._participants[OWN_PARTICIPANT_ID] == (3000.0, 0.0)


def test_missing_import_metric_is_noop():
    host = _host()
    # only export metric known, no import - simulates a partial/corrupt state
    host._metrics["Market/ExportPowerNow"] = Metric(timestamp=1, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=1000.0)
    uplink = _uplink()

    mirror_host_to_uplink(host, uplink, OWN_PARTICIPANT_ID)

    assert uplink.known_participants == ()
