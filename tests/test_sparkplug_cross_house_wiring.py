# -*- coding: utf-8 -*-
"""
Integration tests for the full local-state -> WAN-Uplink wiring (mirrors what
main.py::_run_daemon() wires together, without actually running main.py):

EMSOMAT -> SparkplugHost -> bridge.mirror_host_to_uplink() -> WAN Uplink
    -> (shared FakeBroker) -> WAN Downlink (house B) -> local ParticipantRelay (house B)

Covers the explicit test checklist from the "Letzte Verdrahtung" task.
"""
from __future__ import annotations

from shareomat.config import MqttConfig, SparkplugConfig, WanConfig
from shareomat.sparkplug.bridge import mirror_host_to_uplink
from shareomat.sparkplug.host import SparkplugHost
from shareomat.sparkplug.participant_relay import ParticipantRelay
from shareomat.sparkplug.wan_downlink import WanDownlink
from shareomat.sparkplug.wan_uplink import build_wan_uplink
from shareomat.sparkplug.vendor.datatype import DataType
from shareomat.sparkplug.vendor.metric import Metric
from shareomat.sparkplug.vendor.payload import NBirth, NData

OWN_PARTICIPANT_ID = "P1"
LEG_GROUP_ID = "leg-0001"


# ---------------------------------------------------------------------------
# shared in-memory FakeBroker (same minimal double as test_sparkplug_wan.py -
# duplicated deliberately, these are independent, self-contained test files)
# ---------------------------------------------------------------------------

class FakeBroker:
    def __init__(self):
        self._clients: list["FakeClient"] = []

    def register(self, client: "FakeClient") -> None:
        self._clients.append(client)

    def route(self, sender, topic, payload, qos, retain) -> None:
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
    def __init__(self, broker: FakeBroker):
        self._broker = broker
        self.subscriptions: list[str] = []
        self.on_connect = None
        self.on_message = None
        self.will = None
        self.disconnected = False
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
        self.disconnected = False  # mirrors real paho: a fresh connect() clears prior disconnect state

    def loop_start(self):
        if self.on_connect:
            self.on_connect(self, None, {}, 0)

    def loop_stop(self):
        pass

    def disconnect(self):
        self.disconnected = True

    def publish(self, topic, payload, qos=0, retain=False):
        if self.disconnected:
            return  # WAN outage simulation: publishes silently vanish
        self._broker.route(self, topic, payload, qos, retain)

    def subscribe(self, topic, qos=0):
        self.subscriptions.append(topic)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

def _nbirth_bytes(export_w, import_w, ts_ms):
    metrics = (
        Metric(timestamp=ts_ms, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=export_w),
        Metric(timestamp=ts_ms, name="Market/ImportPowerNow", datatype=DataType.DOUBLE, value=import_w),
    )
    return NBirth(timestamp=ts_ms, seq=0, metrics=metrics).encode(include_dtypes=True)


def _site_a_stack(broker: FakeBroker):
    """Builds house A's full chain: SparkplugHost (receives Emsomat) + WAN
    Uplink, wired exactly like main.py::_run_daemon() does."""
    wan_config = WanConfig(
        enabled=True, broker="mqtt.shareomat.ch", group_id=LEG_GROUP_ID,
        edge_node_id="SITE-A", own_participant_id=OWN_PARTICIPANT_ID,
    )
    uplink_client = FakeClient(broker)
    wan_uplink = build_wan_uplink(wan_config, client_id="site-a-uplink", client=uplink_client)
    wan_uplink.start()

    def _on_emsomat_metrics_changed():
        mirror_host_to_uplink(host, wan_uplink, OWN_PARTICIPANT_ID)

    sp_config = SparkplugConfig(enabled=True, group_id="emsomat", primary_host_id="shareomat", emsomat_edge_node_id="EMSOMAT-A")
    mqtt_config = MqttConfig(enabled=True, broker="localhost", port=1883, client_id="shareomat-a")
    host = SparkplugHost(sp_config, mqtt_config, client=object(), on_metrics_changed=_on_emsomat_metrics_changed)

    return host, wan_uplink


def _site_b_stack(broker: FakeBroker):
    """Builds house B's downlink + local relay (what B's own Emsomat would see)."""
    wan_config = WanConfig(
        enabled=True, broker="mqtt.shareomat.ch", group_id=LEG_GROUP_ID,
        edge_node_id="SITE-B", own_participant_id="P2",
    )
    local_relay_sp_config = SparkplugConfig(enabled=True, group_id="emsomat", participant_edge_node_id="site-b-local-relay")
    local_relay_mqtt_config = MqttConfig(enabled=True, broker="localhost", port=1883, client_id="local-b")
    local_relay = ParticipantRelay(local_relay_sp_config, local_relay_mqtt_config, client=object())

    downlink_client = FakeClient(broker)
    downlink = WanDownlink(wan_config, local_relay, client_id="site-b-downlink", client=downlink_client)
    downlink.start()
    return downlink, local_relay


# ---------------------------------------------------------------------------
# [ ] echter lokaler EMSOMAT-State landet im WAN-Uplink
# [ ] ExportPower korrekt
# [ ] ImportPower korrekt
# ---------------------------------------------------------------------------

def test_local_emsomat_state_reaches_wan_uplink_with_correct_power_values():
    broker = FakeBroker()
    host, wan_uplink = _site_a_stack(broker)

    host._handle_nbirth(_nbirth_bytes(2500.0, 0.0, 1000))

    assert OWN_PARTICIPANT_ID in wan_uplink.known_participants
    assert wan_uplink._participants[OWN_PARTICIPANT_ID] == (2500.0, 0.0)


def test_full_chain_local_emsomat_reaches_remote_house_b():
    broker = FakeBroker()
    host, wan_uplink = _site_a_stack(broker)
    downlink_b, relay_b = _site_b_stack(broker)

    host._handle_nbirth(_nbirth_bytes(2500.0, 100.0, 1000))

    assert OWN_PARTICIPANT_ID in relay_b.known_participants
    assert relay_b._participants[OWN_PARTICIPANT_ID] == (2500.0, 100.0)


# ---------------------------------------------------------------------------
# [ ] Metric-Timestamp bleibt erhalten
# ---------------------------------------------------------------------------

def test_metric_timestamp_preserved_end_to_end():
    broker = FakeBroker()
    host, wan_uplink = _site_a_stack(broker)
    downlink_b, relay_b = _site_b_stack(broker)

    ORIGINAL_TS = 1700000000123
    host._handle_nbirth(_nbirth_bytes(1000.0, 0.0, ORIGINAL_TS))

    remote_birth = downlink_b._remote_births[("SITE-A", OWN_PARTICIPANT_ID)]
    ts = next(m.timestamp for m in remote_birth.metrics if m.name == "Market/ExportPowerNow")
    assert ts == ORIGINAL_TS


# ---------------------------------------------------------------------------
# [ ] Remote Participant gelangt niemals in den Uplink
# ---------------------------------------------------------------------------

def test_remote_participant_never_enters_local_uplink():
    broker = FakeBroker()
    host, wan_uplink = _site_a_stack(broker)
    downlink_b, relay_b = _site_b_stack(broker)

    # House B publishes its own participant via its own uplink
    wan_config_b = WanConfig(enabled=True, broker="mqtt.shareomat.ch", group_id=LEG_GROUP_ID, edge_node_id="SITE-B", own_participant_id="P2")
    uplink_b_client = FakeClient(broker)
    uplink_b = build_wan_uplink(wan_config_b, client_id="site-b-uplink", client=uplink_b_client)
    uplink_b.start()
    uplink_b.register_participant("P2", 700.0, 0.0, timestamp_ms=1000)

    # site A's downlink would also see it if A had one - but check specifically
    # that A's own UPLINK registry (what it publishes about itself) never
    # gained P2 - the uplink has no subscribe/consume path at all.
    assert "P2" not in wan_uplink.known_participants
    assert not hasattr(wan_uplink, "_remote_births")  # ParticipantRelay has no remote-consumption concept


# ---------------------------------------------------------------------------
# [ ] WAN deaktiviert -> lokales System funktioniert unveraendert
# ---------------------------------------------------------------------------

def test_wan_disabled_local_sparkplug_still_works():
    """No wan_uplink/wan_downlink at all - SparkplugHost must keep tracking
    Emsomat's state exactly as before the WAN feature existed."""
    sp_config = SparkplugConfig(enabled=True, group_id="emsomat", primary_host_id="shareomat", emsomat_edge_node_id="EMSOMAT-A")
    mqtt_config = MqttConfig(enabled=True, broker="localhost", port=1883, client_id="shareomat-a")
    host = SparkplugHost(sp_config, mqtt_config, client=object())  # on_metrics_changed=None, as main.py does when wan disabled

    host._handle_nbirth(_nbirth_bytes(1500.0, 0.0, 1000))

    assert host.emsomat_online is True
    assert host.get_metric_value("Market/ExportPowerNow") == 1500.0


# ---------------------------------------------------------------------------
# [ ] WAN-Ausfall -> lokales System funktioniert weiter
# ---------------------------------------------------------------------------

def test_wan_outage_does_not_affect_local_host_tracking():
    broker = FakeBroker()
    host, wan_uplink = _site_a_stack(broker)
    host._handle_nbirth(_nbirth_bytes(1000.0, 0.0, 1000))
    assert OWN_PARTICIPANT_ID in wan_uplink.known_participants

    # simulate WAN outage: uplink's connection drops
    wan_uplink._client.disconnected = True

    # local Emsomat keeps sending updates regardless
    ndata = NData(timestamp=2000, seq=1, metrics=(
        Metric(timestamp=2000, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=3000.0),
        Metric(timestamp=2000, name="Market/ImportPowerNow", datatype=DataType.DOUBLE, value=0.0),
    )).encode(include_dtypes=True)
    host._handle_ndata(ndata)

    # local tracking (SparkplugHost / bridge call) does not raise or block,
    # even though the underlying WAN publish silently failed
    assert host.get_metric_value("Market/ExportPowerNow") == 3000.0
    assert host.emsomat_online is True


def test_wan_uplink_start_failure_does_not_prevent_local_host_creation():
    """Mirrors main.py: if wan_uplink.start() returns False, wan_uplink is
    set to None and SparkplugHost is still constructed (with
    on_metrics_changed=None) - local strecke unaffected."""
    class FailingClient:
        def will_set(self, *a, **k): pass
        def username_pw_set(self, *a, **k): pass
        def tls_set(self, *a, **k): pass
        def reconnect_delay_set(self, *a, **k): pass
        def connect(self, *a, **k): raise ConnectionError("WAN broker unreachable")
        def loop_start(self): pass
        def loop_stop(self): pass
        def disconnect(self): pass

    wan_config = WanConfig(enabled=True, broker="mqtt.shareomat.ch", group_id=LEG_GROUP_ID, edge_node_id="SITE-A", own_participant_id=OWN_PARTICIPANT_ID)
    wan_uplink = build_wan_uplink(wan_config, client_id="site-a-uplink", client=FailingClient())
    started = wan_uplink.start()
    assert started is False

    # main.py would now set wan_uplink = None and build SparkplugHost with on_metrics_changed=None
    sp_config = SparkplugConfig(enabled=True, group_id="emsomat", primary_host_id="shareomat", emsomat_edge_node_id="EMSOMAT-A")
    mqtt_config = MqttConfig(enabled=True, broker="localhost", port=1883, client_id="shareomat-a")
    host = SparkplugHost(sp_config, mqtt_config, client=object(), on_metrics_changed=None)
    host._handle_nbirth(_nbirth_bytes(1000.0, 0.0, 1000))
    assert host.emsomat_online is True  # local tracking unaffected by WAN failure


# ---------------------------------------------------------------------------
# [ ] Reconnect -> aktueller lokaler Participant wird korrekt neu publiziert
# ---------------------------------------------------------------------------

def test_uplink_reconnect_republishes_current_local_participant():
    broker = FakeBroker()
    host, wan_uplink = _site_a_stack(broker)
    downlink_b, relay_b = _site_b_stack(broker)

    host._handle_nbirth(_nbirth_bytes(1200.0, 0.0, 1000))
    assert OWN_PARTICIPANT_ID in relay_b.known_participants

    # simulate uplink disconnect/reconnect (e.g. after a WAN outage recovers)
    wan_uplink.stop()
    assert relay_b.known_participants == ()  # house B correctly saw the NDEATH

    wan_uplink.start()  # re-birth republishes NBIRTH + DBIRTH for still-registered participants
    assert OWN_PARTICIPANT_ID in relay_b.known_participants
    assert relay_b._participants[OWN_PARTICIPANT_ID] == (1200.0, 0.0)
