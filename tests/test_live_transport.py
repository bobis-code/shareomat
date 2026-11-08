# -*- coding: utf-8 -*-
"""LE-04, LE-05, LE-14: invalid values, sessions and source identity on the Sparkplug paths."""
from __future__ import annotations

import math
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from shareomat.config import MqttConfig, SparkplugConfig, WanConfig
from shareomat.sparkplug import participant_relay as relay_module
from shareomat.sparkplug.bridge import mirror_host_to_uplink
from shareomat.sparkplug.host import MAX_LOCAL_PAYLOAD_BYTES, SparkplugHost
from shareomat.sparkplug.participant_relay import ParticipantRelay
from shareomat.sparkplug.vendor.datatype import DataType
from shareomat.sparkplug.vendor.metric import Metric
from shareomat.sparkplug.vendor.payload import DBirth, DData, NBirth, NData, NDeath
from shareomat.sparkplug.wan_downlink import MAX_WAN_METRICS, MAX_WAN_PAYLOAD_BYTES, WanDownlink
from shareomat.sparkplug.wan_uplink import build_wan_uplink

TS = int(datetime(2026, 11, 8, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
EXPORT = "Market/ExportPowerNow"
IMPORT = "Market/ImportPowerNow"
SOURCE = "Market/PowerTimeSource"
GROUP = "leg-1"


class FakeClient:
    def __init__(self):
        self.published = []
        self.subscribed = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))

    def subscribe(self, topic, qos=0):
        self.subscribed.append(topic)


class Sink:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args):
            self.calls.append((name, args))
        return record

    def names(self):
        return [c[0] for c in self.calls]


def metric(name, value, ts=TS, is_null=False):
    return Metric(timestamp=ts, name=name, datatype=DataType.DOUBLE, value=value, is_null=is_null)


def power(export_w, import_w, ts=TS, *, source=None):
    metrics = (
        metric(EXPORT, export_w, ts, is_null=export_w is None),
        metric(IMPORT, import_w, ts, is_null=import_w is None),
    )
    if source:
        metrics += (Metric(timestamp=ts, name=SOURCE, datatype=DataType.STRING, value=source),)
    return metrics


def nbirth(export_w, import_w, seq=0, ts=TS, bdseq=1, source=None):
    bd = Metric(timestamp=None, name="bdSeq", datatype=DataType.INT64, value=bdseq)
    return NBirth(timestamp=ts, seq=seq, metrics=(bd,) + power(export_w, import_w, ts, source=source)).encode(include_dtypes=True)


def ndata(export_w, import_w, seq=1, ts=TS, source=None):
    return NData(timestamp=ts, seq=seq, metrics=power(export_w, import_w, ts, source=source)).encode(include_dtypes=True)


def host(sink=None, client=None):
    sp = SparkplugConfig(enabled=True, group_id="emsomat", primary_host_id="shareomat", emsomat_edge_node_id="EMS")
    return SparkplugHost(sp, MqttConfig(enabled=True, broker="b", port=1883, client_id="c"), client=client or FakeClient(), live_sink=sink)


def connected_uplink():
    wan = WanConfig(enabled=True, broker="b", group_id=GROUP, edge_node_id="SITE-A", own_participant_id="P1")
    uplink = build_wan_uplink(wan, client_id="u")
    uplink._client = FakeClient()
    uplink._connected.set()
    return uplink


def local_relay():
    sp = SparkplugConfig(enabled=True, group_id="emsomat", primary_host_id="shareomat", emsomat_edge_node_id="EMS", participant_edge_node_id="relay")
    relay = ParticipantRelay(sp, MqttConfig(enabled=True, broker="b", port=1883, client_id="c"))
    relay._client = FakeClient()
    relay._connected.set()
    return relay


def downlink(allowed=None, sink=None):
    relay = local_relay()
    wan = WanConfig(enabled=True, broker="b", group_id=GROUP, edge_node_id="SITE-A", own_participant_id="P1")
    return WanDownlink(wan, relay, client_id="d", allowed_source=allowed or (lambda e, d: (e, d) == ("SITE-B", "P2")),
                       client=FakeClient(), live_sink=sink), relay


def msg(kind, edge, device, payload, retain=False):
    return SimpleNamespace(topic=f"spBv1.0/{GROUP}/{kind}/{edge}/{device}", payload=payload, retain=retain)


def dbirth(export_w=100.0, import_w=0.0, ts=TS, source=None):
    return DBirth(timestamp=ts, seq=1, metrics=power(export_w, import_w, ts, source=source)).encode(include_dtypes=True)


def ddata(export_w=100.0, import_w=0.0, ts=TS, source=None):
    return DData(timestamp=ts, seq=2, metrics=power(export_w, import_w, ts, source=source)).encode(include_dtypes=True)


def decoded(client, kind):
    payloads = [p for t, p, _, _ in client.published if f"/{kind}/" in t]
    return {"DBIRTH": DBirth, "DDATA": DData}[kind].decode(payloads[-1])


def by_name(message):
    return {m.name: m for m in message.metrics}


# ── LE-14 ───────────────────────────────────────────────────────────────────


def test_ndeath_timestamp_survives_decoding():
    bd = Metric(timestamp=None, name="bdSeq", datatype=DataType.INT64, value=3)
    assert NDeath.decode(NDeath(timestamp=TS, bd_seq_metric=bd).encode()).timestamp == TS


def test_ndeath_without_timestamp_stays_none():
    bd = Metric(timestamp=None, name="bdSeq", datatype=DataType.INT64, value=3)
    assert NDeath.decode(NDeath(timestamp=None, bd_seq_metric=bd).encode()).timestamp is None


# ── LE-04: bridge and relay ─────────────────────────────────────────────────


def test_bridge_keeps_is_null_and_real_zero():
    h = host()
    h._handle_nbirth(nbirth(None, 0.0))
    uplink = connected_uplink()
    mirror_host_to_uplink(h, uplink, "P1")
    assert uplink._participants["P1"] == (None, 0.0)
    fields = by_name(decoded(uplink._client, "DBIRTH"))
    assert fields[EXPORT].is_null and fields[EXPORT].value is None
    assert not fields[IMPORT].is_null and fields[IMPORT].value == 0.0


def test_bridge_passes_source_timestamp_and_time_source():
    h = host()
    h._handle_nbirth(nbirth(1500.0, 0.0, ts=TS - 7000, source="ha_last_reported"))
    uplink = connected_uplink()
    mirror_host_to_uplink(h, uplink, "P1")
    fields = by_name(decoded(uplink._client, "DBIRTH"))
    assert fields[EXPORT].timestamp == TS - 7000 and fields[IMPORT].timestamp == TS - 7000
    assert fields[SOURCE].value == "ha_last_reported"


@pytest.mark.parametrize("bad", [math.nan, math.inf, -5.0])
def test_bridge_sends_bad_numbers_as_null(bad):
    h = host()
    h._handle_nbirth(nbirth(bad, 0.0))
    uplink = connected_uplink()
    mirror_host_to_uplink(h, uplink, "P1")
    assert uplink._participants["P1"][0] is None


def test_bridge_value_without_timestamp_is_not_stamped_now():
    h = host()
    h._handle_nbirth(NBirth(timestamp=TS, seq=0, metrics=(metric(EXPORT, 100.0, None), metric(IMPORT, 0.0, None))).encode(include_dtypes=True))
    uplink = connected_uplink()
    mirror_host_to_uplink(h, uplink, "P1")
    assert uplink._participants["P1"] == (None, None)
    assert by_name(decoded(uplink._client, "DBIRTH"))[EXPORT].timestamp is None


def test_bridge_removes_participant_when_emsomat_goes_away():
    h = host()
    bd = Metric(timestamp=None, name="bdSeq", datatype=DataType.INT64, value=1)
    h._handle_nbirth(nbirth(100.0, 0.0))
    uplink = connected_uplink()
    mirror_host_to_uplink(h, uplink, "P1")
    h._handle_ndeath(NDeath(timestamp=None, bd_seq_metric=bd).encode())
    mirror_host_to_uplink(h, uplink, "P1")
    assert uplink.known_participants == ()
    assert any("/DDEATH/" in t for t, *_ in uplink._client.published)


def test_relay_rebirth_keeps_original_timestamp():
    uplink = connected_uplink()
    uplink.register_participant("P1", 100.0, 0.0, timestamp_ms=TS - 60000, time_source="ha_last_reported")
    uplink._client.published.clear()
    assert uplink.republish_births_if_due(interval_s=0.0)
    fields = by_name(decoded(uplink._client, "DBIRTH"))
    assert fields[EXPORT].timestamp == TS - 60000 and fields[SOURCE].value == "ha_last_reported"
    assert any("/NBIRTH/" in t for t, *_ in uplink._client.published)


def test_wan_rebirth_only_when_due_and_only_on_wan():
    uplink = connected_uplink()
    assert uplink.republish_births_if_due(interval_s=0.0)
    assert not uplink.republish_births_if_due(interval_s=1000.0)
    assert not local_relay().republish_births_if_due(interval_s=0.0)


def test_relay_stops_publishing_after_disconnect():
    uplink = connected_uplink()
    uplink._on_disconnect(uplink._client, None, 1)
    uplink.register_participant("P1", 1.0, 0.0, timestamp_ms=TS)
    assert uplink._client.published == []


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0, "x", True])
def test_relay_never_publishes_a_bad_number(bad):
    uplink = connected_uplink()
    uplink.register_participant("P1", bad, 0.0, timestamp_ms=TS)
    assert by_name(decoded(uplink._client, "DBIRTH"))[EXPORT].is_null


# ── LE-04/05: WAN downlink ──────────────────────────────────────────────────


def test_downlink_requires_an_explicit_allow_rule():
    relay = local_relay()
    wan = WanConfig(enabled=True, broker="b", group_id=GROUP, edge_node_id="A", own_participant_id="P")
    with pytest.raises(TypeError):
        WanDownlink(wan, relay, client_id="d")


def test_downlink_passes_null_on_never_zero():
    d, relay = downlink()
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", dbirth(None, 300.0)))
    assert relay._participants["P2"] == (None, 300.0)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -3.0])
def test_downlink_turns_bad_numbers_into_null(bad):
    d, relay = downlink()
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", dbirth(bad, 0.0)))
    assert relay._participants["P2"][0] is None


def test_downlink_passes_time_source_on():
    d, relay = downlink()
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", dbirth(1.0, 0.0, source="ha_last_reported")))
    assert relay._participant_meta["P2"] == (TS, "ha_last_reported")


def test_unregistered_source_is_dropped_and_listed():
    sink = Sink()
    d, relay = downlink(sink=sink)
    d._on_message(None, None, msg("DBIRTH", "SITE-EVIL", "P2", dbirth()))
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P9", dbirth()))
    assert relay.known_participants == () and sink.calls == []
    assert set(d.unknown_pairs) == {("SITE-EVIL", "P2"), ("SITE-B", "P9")}


def test_foreign_site_cannot_overwrite_or_kill_a_registered_participant():
    d, relay = downlink()
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", dbirth(100.0, 0.0)))
    d._on_message(None, None, msg("DDATA", "SITE-EVIL", "P2", ddata(9999.0, 0.0)))
    d._on_message(None, None, SimpleNamespace(topic=f"spBv1.0/{GROUP}/DDEATH/SITE-EVIL/P2", payload=b"", retain=False))
    assert relay._participants["P2"] == (100.0, 0.0)


def test_unknown_pair_list_is_bounded():
    d, _ = downlink()
    for i in range(100):
        d._on_message(None, None, msg("DBIRTH", f"S{i}", "X", dbirth()))
    assert len(d.unknown_pairs) == 32


def test_retained_and_oversized_messages_are_dropped():
    d, relay = downlink()
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", dbirth(), retain=True))
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", b"x" * (MAX_WAN_PAYLOAD_BYTES + 1)))
    assert relay.known_participants == ()


def test_message_with_too_many_metrics_is_ignored():
    d, relay = downlink()
    many = tuple(metric(f"x{i}", 1.0) for i in range(MAX_WAN_METRICS + 1)) + power(1.0, 0.0)
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", DBirth(timestamp=TS, seq=1, metrics=many).encode(include_dtypes=True)))
    assert relay.known_participants == ()


def test_garbage_payload_does_not_raise():
    d, relay = downlink()
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", b"\xff\xfe not protobuf"))
    d._on_message(None, None, SimpleNamespace(topic="garbage", payload=b"", retain=False))
    assert relay.known_participants == ()


def test_ddata_without_birth_is_ignored():
    d, relay = downlink()
    d._on_message(None, None, msg("DDATA", "SITE-B", "P2", ddata()))
    assert relay.known_participants == ()


def test_broker_loss_removes_remote_participants_and_tells_live_layer():
    sink = Sink()
    d, relay = downlink(sink=sink)
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", dbirth()))
    d._on_disconnect(d._client, None, 1)
    assert relay.known_participants == () and d.known_remote_edge_nodes == ()
    assert ("on_connection", (False,)) in sink.calls


def test_ndeath_tells_live_layer_per_device():
    sink = Sink()
    d, relay = downlink(sink=sink)
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", dbirth()))
    d._handle_ndeath(SimpleNamespace(edge_node_id="SITE-B"))
    assert ("on_device_death", ("SITE-B", "P2")) in sink.calls and relay.known_participants == ()


def test_allow_rule_exception_means_dropped():
    def broken(edge, device):
        raise RuntimeError("kaputt")
    d, relay = downlink(allowed=broken)
    d._on_message(None, None, msg("DBIRTH", "SITE-B", "P2", dbirth()))
    assert relay.known_participants == ()


# ── LE-05: host ─────────────────────────────────────────────────────────────


def host_msg(h, kind, payload, retain=False):
    return SimpleNamespace(topic=h._node_topic(kind), payload=payload, retain=retain)


def test_host_drops_retained_and_oversized():
    from shareomat.sparkplug.vendor.enums import MessageType
    h = host()
    h._on_message(None, None, host_msg(h, MessageType.NBIRTH, nbirth(1.0, 0.0), retain=True))
    h._on_message(None, None, host_msg(h, MessageType.NBIRTH, b"x" * (MAX_LOCAL_PAYLOAD_BYTES + 1)))
    assert not h.emsomat_online


def test_new_birth_clears_old_metrics():
    h = host()
    h._handle_nbirth(nbirth(1.0, 0.0))
    h._metrics["Old/Metric"] = metric("Old/Metric", 5.0)
    h._handle_nbirth(nbirth(2.0, 0.0))
    assert h.get_metric("Old/Metric") is None and h.get_metric(EXPORT).value == 2.0


def test_ndata_without_birth_is_ignored_and_requests_rebirth():
    client = FakeClient()
    h = host(client=client)
    h._connected.set()
    h._handle_ndata(ndata(1.0, 0.0))
    assert h.get_metric(EXPORT) is None
    assert any("/NCMD/" in t for t, *_ in client.published)


def test_broker_loss_invalidates_values_and_tells_live_layer():
    sink = Sink()
    h = host(sink=sink)
    h._connected.set()
    h._handle_nbirth(nbirth(1.0, 0.0))
    h._on_disconnect(h._client, None, 1)
    assert not h.emsomat_online and h.get_metric(EXPORT) is None
    assert ("on_connection", (False,)) in sink.calls


def test_reconnect_requests_rebirth_first_connect_does_not():
    client = FakeClient()
    h = host(client=client)
    h._on_connect(client, None, {}, 0)
    assert not any("/NCMD/" in t for t, *_ in client.published)
    h._on_disconnect(client, None, 1)
    h._on_connect(client, None, {}, 0)
    assert any("/NCMD/" in t for t, *_ in client.published)


def test_seq_gap_requests_rebirth_limited_and_does_not_block_values():
    client = FakeClient()
    sink = Sink()
    h = host(sink=sink, client=client)
    h._connected.set()
    h._handle_nbirth(nbirth(1.0, 0.0))
    h._handle_ndata(ndata(5.0, 0.0, seq=9))
    h._handle_ndata(ndata(6.0, 0.0, seq=20))
    assert sum("/NCMD/" in t for t, *_ in client.published) == 1
    assert h.get_metric(EXPORT).value == 6.0 and sink.names().count("on_data") == 2


def test_stale_ndeath_keeps_session():
    h = host()
    h._handle_nbirth(nbirth(1.0, 0.0, bdseq=5))
    bd = Metric(timestamp=None, name="bdSeq", datatype=DataType.INT64, value=4)
    h._handle_ndeath(NDeath(timestamp=None, bd_seq_metric=bd).encode())
    assert h.emsomat_online and h.get_metric(EXPORT) is not None


def test_failing_live_sink_never_breaks_the_host():
    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("kaputt")
    h = host(sink=Broken())
    h._handle_nbirth(nbirth(1.0, 0.0))
    assert h.emsomat_online


def test_relay_module_constant_documents_interval():
    assert relay_module.WAN_REBIRTH_INTERVAL_S == 300.0
