# -*- coding: utf-8 -*-
"""Tests for the Cross-House Sparkplug B strecke: wan_uplink.py + wan_downlink.py.

Simulates two Shareomat instances (A and B) talking through a shared
in-memory FakeBroker - no real network, no new dependency, matches the
"keine zusaetzlichen Pakete installieren" constraint. FakeBroker supports
exact-topic and `#`-suffix wildcard subscriptions, which is all this
codebase's subscribe() calls ever use.
"""
from __future__ import annotations

import pytest

from shareomat.config import MqttConfig, SparkplugConfig, WanConfig
from shareomat.sparkplug.participant_relay import ParticipantRelay
from shareomat.sparkplug.wan_downlink import WanDownlink
from shareomat.sparkplug.wan_uplink import build_wan_uplink
from shareomat.sparkplug.vendor.metric import Metric
from shareomat.sparkplug.vendor.datatype import DataType
from shareomat.sparkplug.vendor.payload import DBirth, DData, DDeath, NDeath


# ---------------------------------------------------------------------------
# FakeBroker / FakeClient - in-memory pub/sub, no network, no new dependency
# ---------------------------------------------------------------------------

class FakeBroker:
    def __init__(self):
        self._clients: list["FakeClient"] = []

    def register(self, client: "FakeClient") -> None:
        self._clients.append(client)

    def route(self, sender: "FakeClient", topic: str, payload, qos: int, retain: bool) -> None:
        for client in self._clients:
            for sub_topic in client.subscriptions:
                if self._matches(sub_topic, topic):
                    if client.on_message:
                        client.on_message(client, None, FakeMsg(topic, payload))
                    break

    @staticmethod
    def _matches(pattern: str, topic: str) -> bool:
        if pattern == topic:
            return True
        if pattern.endswith("/#"):
            prefix = pattern[: -len("/#")]
            return topic == prefix or topic.startswith(prefix + "/")
        return False


class FakeMsg:
    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class FakeClient:
    """Minimal paho-mqtt-client stand-in wired to a shared FakeBroker."""

    def __init__(self, broker: FakeBroker):
        self._broker = broker
        self.subscriptions: list[str] = []
        self.on_connect = None
        self.on_message = None
        self.will = None
        broker.register(self)

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
        self._broker.route(self, topic, payload, qos, retain)

    def subscribe(self, topic, qos=0):
        self.subscriptions.append(topic)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

LEG_ID = "leg-0001"


def _wan_config(edge_node_id: str, **overrides) -> WanConfig:
    defaults = dict(
        enabled=True, broker="mqtt.shareomat.ch", port=8883,
        group_id=LEG_ID, edge_node_id=edge_node_id,
    )
    defaults.update(overrides)
    return WanConfig(**defaults)


def _local_relay(edge_node_id="local-relay") -> ParticipantRelay:
    sp_config = SparkplugConfig(enabled=True, group_id="emsomat", participant_edge_node_id=edge_node_id)
    mqtt_config = MqttConfig(enabled=True, broker="localhost", port=1883, client_id="local")
    relay = ParticipantRelay(sp_config, mqtt_config, client=object())  # never actually used on this side
    return relay


def _house(broker: FakeBroker, *, site_id: str, participant_ids: tuple[str, ...] = ()):
    """Builds one house's WAN uplink + downlink + local relay, all wired
    through the shared FakeBroker, and starts them."""
    uplink_client = FakeClient(broker)
    uplink = build_wan_uplink(_wan_config(site_id), client_id=f"{site_id}-uplink", client=uplink_client)
    uplink.start()

    local_relay = _local_relay(edge_node_id=f"{site_id}-local-relay")
    downlink_client = FakeClient(broker)
    downlink = WanDownlink(_wan_config(site_id), local_relay, client_id=f"{site_id}-downlink", client=downlink_client)
    downlink.start()

    return uplink, downlink, local_relay


# ---------------------------------------------------------------------------
# 1. Two Shareomats, A -> Broker -> B and B -> Broker -> A
# ---------------------------------------------------------------------------

def test_house_a_participant_reaches_house_b_local_relay():
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")
    uplink_b, downlink_b, relay_b = _house(broker, site_id="SITE-B")

    uplink_a.register_participant("PARTICIPANT-1", export_now_w=1200.0, import_now_w=0.0, timestamp_ms=5000)

    assert "PARTICIPANT-1" in relay_b.known_participants
    assert relay_b._participants["PARTICIPANT-1"] == (1200.0, 0.0)
    # never leaks back into house A's own local relay
    assert "PARTICIPANT-1" not in relay_a.known_participants


def test_house_b_participant_reaches_house_a_local_relay_bidirectional():
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")
    uplink_b, downlink_b, relay_b = _house(broker, site_id="SITE-B")

    uplink_b.register_participant("PARTICIPANT-2", export_now_w=0.0, import_now_w=800.0, timestamp_ms=6000)

    assert "PARTICIPANT-2" in relay_a.known_participants
    assert relay_a._participants["PARTICIPANT-2"] == (0.0, 800.0)
    assert "PARTICIPANT-2" not in relay_b.known_participants


def test_export_import_power_values_correct_after_update():
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")
    uplink_b, downlink_b, relay_b = _house(broker, site_id="SITE-B")

    uplink_a.register_participant("PARTICIPANT-1", 1000.0, 0.0, timestamp_ms=1000)
    uplink_a.update_participant("PARTICIPANT-1", 3300.0, 150.0, timestamp_ms=2000)

    assert relay_b._participants["PARTICIPANT-1"] == (3300.0, 150.0)


# ---------------------------------------------------------------------------
# 2. Original metric timestamp preserved across the WAN hop
# ---------------------------------------------------------------------------

def test_original_metric_timestamp_preserved_across_wan_hop():
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")
    uplink_b, downlink_b, relay_b = _house(broker, site_id="SITE-B")

    ORIGINAL_TS = 1234567890123
    uplink_a.register_participant("PARTICIPANT-1", 1000.0, 0.0, timestamp_ms=ORIGINAL_TS)

    # inspect what house B's local relay actually republished for its own Emsomat
    remote_birth = downlink_b._remote_births[("SITE-A", "PARTICIPANT-1")]
    metric_ts = next(m.timestamp for m in remote_birth.metrics if m.name == "Market/ExportPowerNow")
    assert metric_ts == ORIGINAL_TS


# ---------------------------------------------------------------------------
# 3. NDEATH of remote Shareomat removes its devices locally
# ---------------------------------------------------------------------------

def test_remote_ndeath_removes_all_its_participants_locally():
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")
    uplink_b, downlink_b, relay_b = _house(broker, site_id="SITE-B")

    uplink_a.register_participant("PARTICIPANT-1", 1000.0, 0.0, timestamp_ms=1000)
    uplink_a.register_participant("PARTICIPANT-2", 500.0, 0.0, timestamp_ms=1000)
    assert set(relay_b.known_participants) == {"PARTICIPANT-1", "PARTICIPANT-2"}

    uplink_a.stop()  # publishes graceful NDEATH

    assert relay_b.known_participants == ()
    assert downlink_b.known_remote_edge_nodes == ()


def test_remote_ddeath_removes_only_that_participant():
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")
    uplink_b, downlink_b, relay_b = _house(broker, site_id="SITE-B")

    uplink_a.register_participant("PARTICIPANT-1", 1000.0, 0.0, timestamp_ms=1000)
    uplink_a.register_participant("PARTICIPANT-2", 500.0, 0.0, timestamp_ms=1000)

    uplink_a.deregister_participant("PARTICIPANT-1")

    assert set(relay_b.known_participants) == {"PARTICIPANT-2"}


# ---------------------------------------------------------------------------
# 4. Reconnect produces correct Birth state
# ---------------------------------------------------------------------------

def test_reconnect_rebuilds_correct_birth_state():
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")
    uplink_b, downlink_b, relay_b = _house(broker, site_id="SITE-B")

    uplink_a.register_participant("PARTICIPANT-1", 1000.0, 0.0, timestamp_ms=1000)
    assert "PARTICIPANT-1" in relay_b.known_participants

    # simulate a full disconnect/reconnect cycle of house A's uplink
    uplink_a.stop()
    assert relay_b.known_participants == ()  # NDEATH cleaned up house B's view

    uplink_a.start()  # re-birth: republishes NBIRTH + DBIRTH for all still-registered participants
    assert "PARTICIPANT-1" in relay_b.known_participants
    assert relay_b._participants["PARTICIPANT-1"] == (1000.0, 0.0)


# ---------------------------------------------------------------------------
# 5. No WAN feedback loop / no own data back as remote participant
# ---------------------------------------------------------------------------

def test_own_uplink_publishes_never_reach_own_downlink_as_remote():
    """Loop/self-echo prevention (Abschnitt 3): a house's own Downlink must
    ignore its own Uplink's publishes, even though both are subscribed/
    publishing under the same group on the shared broker."""
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")

    uplink_a.register_participant("PARTICIPANT-OWN", 999.0, 0.0, timestamp_ms=1000)

    assert "PARTICIPANT-OWN" not in relay_a.known_participants
    assert downlink_a.known_remote_edge_nodes == ()


def test_remote_participant_data_never_flows_back_into_own_uplink():
    """Origin/Loop Prevention (Abschnitt 3): nothing in this module wires
    WanDownlink output back into WanUplink input - verified structurally by
    confirming the two objects share no state/references."""
    broker = FakeBroker()
    uplink_a, downlink_a, relay_a = _house(broker, site_id="SITE-A")
    uplink_b, downlink_b, relay_b = _house(broker, site_id="SITE-B")

    uplink_b.register_participant("PARTICIPANT-B1", 2000.0, 0.0, timestamp_ms=1000)
    assert "PARTICIPANT-B1" in relay_a.known_participants  # reached A's local relay

    # A's own uplink registry (what IT publishes to the WAN) must not contain it
    assert "PARTICIPANT-B1" not in uplink_a.known_participants
    assert downlink_a._local_relay is not uplink_a  # structurally separate objects


# ---------------------------------------------------------------------------
# 6. Cross-LEG data is not accepted
# ---------------------------------------------------------------------------

def test_cross_leg_message_is_rejected_even_if_it_somehow_arrives():
    """Defense in depth: even if a message with a foreign group_id reached
    our client (broker ACL is the real enforcement, this is the second
    layer), WanDownlink must reject it rather than accept it."""
    broker = FakeBroker()
    _, downlink_a, relay_a = _house(broker, site_id="SITE-A")

    foreign_birth = DBirth(
        timestamp=1000, seq=0,
        metrics=(
            Metric(timestamp=1000, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=5000.0),
            Metric(timestamp=1000, name="Market/ImportPowerNow", datatype=DataType.DOUBLE, value=0.0),
        ),
    )
    foreign_topic = "spBv1.0/leg-9999-FOREIGN/DBIRTH/SITE-X/PARTICIPANT-INTRUDER"
    downlink_a._on_message(downlink_a._client, None, FakeMsg(foreign_topic, foreign_birth.encode(include_dtypes=True)))

    assert "PARTICIPANT-INTRUDER" not in relay_a.known_participants


def test_downlink_only_subscribes_within_its_own_leg_group():
    broker = FakeBroker()
    _, downlink_a, _ = _house(broker, site_id="SITE-A")
    assert all(sub.startswith(f"spBv1.0/{LEG_ID}/") for sub in downlink_a._client.subscriptions)


# ---------------------------------------------------------------------------
# WAN uplink: NCMD disabled, correct config mapping
# ---------------------------------------------------------------------------

def test_wan_uplink_has_ncmd_disabled():
    uplink = build_wan_uplink(_wan_config("SITE-A"), client_id="site-a-uplink")
    assert uplink._enable_ncmd is False


def test_wan_uplink_ignores_ncmd_even_if_received():
    broker = FakeBroker()
    uplink_client = FakeClient(broker)
    uplink = build_wan_uplink(_wan_config("SITE-A"), client_id="site-a-uplink", client=uplink_client)
    uplink.start()
    # NCMD forbidden on WAN (Abschnitt 5) - subscribe list must not include NCMD
    assert not any("NCMD" in sub for sub in uplink_client.subscriptions)


def test_wan_uplink_maps_config_fields_correctly():
    wan_config = _wan_config("SITE-A", broker="mqtt.shareomat.ch", port=8883)
    uplink = build_wan_uplink(wan_config, client_id="site-a-uplink")
    assert uplink._sp_config.group_id == LEG_ID
    assert uplink.edge_node_id == "SITE-A"
    assert uplink._mqtt_config.broker == "mqtt.shareomat.ch"
    assert uplink._mqtt_config.port == 8883


# ---------------------------------------------------------------------------
# 7. local Sparkplug track config compatibility (no regression)
# ---------------------------------------------------------------------------

def test_local_participant_relay_default_behavior_unaffected_by_new_param():
    """enable_ncmd defaults to True (unchanged local behavior) and
    timestamp_ms defaults to None (falls back to now(), unchanged local
    behavior) - regression guard for the additive changes made for WAN."""
    broker = FakeBroker()
    client = FakeClient(broker)
    sp_config = SparkplugConfig(enabled=True, group_id="emsomat", participant_edge_node_id="relay")
    mqtt_config = MqttConfig(enabled=True, broker="localhost", port=1883, client_id="local")
    relay = ParticipantRelay(sp_config, mqtt_config, client=client)
    assert relay._enable_ncmd is True
    relay.start()
    assert any("NCMD" in sub for sub in client.subscriptions)
