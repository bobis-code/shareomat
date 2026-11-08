# -*- coding: utf-8 -*-
"""
File: shareomat/sparkplug/host.py

SparkplugHost - Shareomat as Sparkplug B Primary Host Application for the
local Emsomat channel. See docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md
Abschnitt 4/28.2 for the full specification.

Own MQTT client connection, separate from shareomat/ha/mqtt_runtime.py's
(that one already registers its own Will for `{prefix}/status` - MQTT allows
only one Will per connection, so the Sparkplug STATE Will needs its own
connection to the same broker).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Callable, Optional

from shareomat.config import MqttConfig, SparkplugConfig

from .vendor.datatype import DataType
from .vendor.enums import MessageType
from .vendor.metric import Metric
from .vendor.payload import NBirth, NCmd, NData, NDeath, State
from .vendor.time_utils import get_current_timestamp
from .vendor.topic import Topic

if TYPE_CHECKING:
    import paho.mqtt.client as _mqtt_type

logger = logging.getLogger(__name__)

METRIC_BDSEQ = "bdSeq"
METRIC_NODE_REBIRTH = "Node Control/Rebirth"

_QOS_NON_STATE = 0
_QOS_STATE = 1

# Upper bound for one local Sparkplug message (NBIRTH carries the forecasts).
MAX_LOCAL_PAYLOAD_BYTES = 1_048_576
# At most one Rebirth request per interval after a sequence gap.
_REBIRTH_MIN_INTERVAL_S = 60.0
# stop() waits at most this long for a publish that is under way.
_STOP_LOCK_TIMEOUT_S = 2.0

try:
    import paho.mqtt.client as mqtt
    _PAHO_AVAILABLE = True
    try:
        _CB_API_V1 = mqtt.CallbackAPIVersion.VERSION1
    except AttributeError:
        _CB_API_V1 = None
except ImportError:
    _PAHO_AVAILABLE = False
    _CB_API_V1 = None


def _make_client(client_id: str) -> "_mqtt_type.Client":
    """Same paho v1/v2 compatibility pattern as shareomat/ha/mqtt_runtime.py."""
    if _CB_API_V1 is not None:
        return mqtt.Client(callback_api_version=_CB_API_V1, client_id=client_id)
    return mqtt.Client(client_id=client_id)


class SparkplugHost:
    """Primary Host Application role: publishes STATE, tracks Emsomat's
    NBIRTH/NDATA/NDEATH lifecycle, can request a Rebirth.

    `client` can be injected for tests (any object exposing will_set/
    publish/subscribe/connect/loop_start/loop_stop/disconnect, plus
    on_connect/on_message/on_disconnect callback attributes) - production
    code leaves it None and a real paho client is created in start().
    """

    def __init__(
        self,
        sp_config: SparkplugConfig,
        mqtt_config: MqttConfig,
        *,
        client: Optional["_mqtt_type.Client"] = None,
        on_metrics_changed: Optional[Callable[[], None]] = None,
        live_sink: Optional[object] = None,
    ):
        self._sp_config = sp_config
        self._mqtt_config = mqtt_config
        self._client = client
        self._on_metrics_changed = on_metrics_changed
        self._connected = threading.Event()
        self._emsomat_online = False
        self._emsomat_birth: Optional[NBirth] = None
        self._last_known_bdseq: Optional[int] = None
        self._metrics: dict[str, Metric] = {}
        # Live-Ingest (shareomat.live.adapters): on_birth/on_data/on_death/on_connection
        self._live_sink = live_sink
        self._ever_connected = False
        self._expected_seq: Optional[int] = None
        self._last_rebirth_request: Optional[float] = None
        # Check-and-publish and stop() exclude each other: after stop() nothing is sent.
        self._publish_lock = threading.Lock()
        self._stopped = False

    # ------------------------------------------------------------------
    # topics
    # ------------------------------------------------------------------
    @property
    def _state_topic(self) -> str:
        return str(Topic(message_type=MessageType.STATE, sparkplug_host_id=self._sp_config.primary_host_id))

    def _node_topic(self, message_type: MessageType) -> str:
        return str(Topic(
            group_id=self._sp_config.group_id,
            message_type=message_type,
            edge_node_id=self._sp_config.emsomat_edge_node_id,
        ))

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, *, timeout: float = 5.0) -> bool:
        """Connects (own client, own Will), publishes STATE ONLINE, subscribes
        to Emsomat's NBIRTH/NDATA/NDEATH. Returns False if connecting fails
        or paho-mqtt isn't installed."""
        if not self._sp_config.enabled:
            return False
        if self._client is None:
            if not _PAHO_AVAILABLE:
                logger.error("paho-mqtt not installed - cannot start SparkplugHost")
                return False
            self._client = _make_client(f"{self._mqtt_config.client_id}-sparkplug-host")

        self._client.will_set(self._state_topic, State(online=False).encode(), qos=_QOS_STATE, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        if self._mqtt_config.username:
            self._client.username_pw_set(self._mqtt_config.username, self._mqtt_config.password or None)
        if self._mqtt_config.tls_enabled:
            self._client.tls_set(ca_certs=self._mqtt_config.tls_ca_cert or None)
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)

        try:
            self._client.connect(self._mqtt_config.broker, self._mqtt_config.port, keepalive=60)
        except Exception as exc:
            logger.error("SparkplugHost MQTT connect failed: %s", exc)
            return False
        self._client.loop_start()

        if not self._connected.wait(timeout=timeout):
            logger.error("SparkplugHost connection timeout - broker unreachable")
            self._client.loop_stop()
            return False
        return True

    def stop(self) -> None:
        """Publishes STATE OFFLINE explicitly (graceful, timely - not just
        relying on the Will at TCP timeout, same reasoning as Emsomat's
        EdgeNodeAdapter.async_stop()) and disconnects. Safe to call twice."""
        if self._client is None:
            return
        locked = self._publish_lock.acquire(timeout=_STOP_LOCK_TIMEOUT_S)
        if not locked:
            logger.warning("SparkplugHost.stop: a publish did not finish in time, stopping anyway")
        try:
            if self._stopped:
                return
            self._stopped = True
            self._publish_state_offline()
        finally:
            if locked:
                self._publish_lock.release()
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass
        self._connected.clear()

    def _publish_state_offline(self) -> None:
        if self._connected.is_set():
            try:
                self._client.publish(self._state_topic, State(online=False).encode(), qos=_QOS_STATE, retain=True)
            except Exception:
                logger.exception("SparkplugHost: failed publishing graceful STATE offline")

    def _on_connect(self, client, userdata, flags, rc=0, properties=None) -> None:
        ok = (int(rc) == 0) if not hasattr(rc, "value") else (rc.value == 0)
        if not ok:
            logger.error("SparkplugHost connect failed: rc=%s", rc)
            return
        self._connected.set()
        client.publish(self._state_topic, State(online=True).encode(), qos=_QOS_STATE, retain=True)
        for message_type in (MessageType.NBIRTH, MessageType.NDATA, MessageType.NDEATH):
            client.subscribe(self._node_topic(message_type), qos=_QOS_NON_STATE)
        logger.info("SparkplugHost connected, STATE=ONLINE published, subscribed to Emsomat node topics")
        self._notify_live_connection(True)
        if self._ever_connected:
            # A death or birth may have been missed while disconnected.
            self.request_rebirth()
        self._ever_connected = True

    def _on_disconnect(self, client, userdata, rc=0, properties=None) -> None:
        """Lost the broker: nothing we hold can still be trusted as current."""
        self._connected.clear()
        self._forget_emsomat_session()
        logger.warning("SparkplugHost disconnected from broker (rc=%s) - Emsomat values invalidated", rc)
        self._notify_live_connection(False)
        self._notify_metrics_changed()

    def _forget_emsomat_session(self) -> None:
        self._emsomat_online = False
        self._emsomat_birth = None
        self._metrics.clear()
        self._expected_seq = None

    def _notify_live_connection(self, connected: bool) -> None:
        if self._live_sink is None:
            return
        try:
            self._live_sink.on_connection(connected)
        except Exception:
            logger.debug("SparkplugHost live sink on_connection failed", exc_info=True)

    def _notify_live(self, method: str, *args) -> None:
        if self._live_sink is None:
            return
        try:
            getattr(self._live_sink, method)(*args)
        except Exception:
            logger.debug("SparkplugHost live sink %s failed", method, exc_info=True)

    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic
        # Sparkplug data is never retained: a retained copy would be an old
        # value that looks new after a restart (ADR-0007, 4.1 rule 7).
        if getattr(msg, "retain", False):
            logger.warning("SparkplugHost: discarded retained message on %r", topic)
            return
        if len(msg.payload) > MAX_LOCAL_PAYLOAD_BYTES:
            logger.warning("SparkplugHost: discarded oversized message on %r (%d bytes)", topic, len(msg.payload))
            return
        try:
            if topic == self._node_topic(MessageType.NBIRTH):
                self._handle_nbirth(msg.payload)
            elif topic == self._node_topic(MessageType.NDATA):
                self._handle_ndata(msg.payload)
            elif topic == self._node_topic(MessageType.NDEATH):
                self._handle_ndeath(msg.payload)
        except Exception:
            logger.warning("SparkplugHost: failed handling message on %s", topic, exc_info=True)

    # ------------------------------------------------------------------
    # Emsomat lifecycle tracking (pure logic, directly unit-testable)
    # ------------------------------------------------------------------
    def _handle_nbirth(self, raw: bytes) -> None:
        birth = NBirth.decode(raw)
        self._emsomat_birth = birth
        # A new session: nothing from the previous one may survive.
        self._metrics.clear()
        self._expected_seq = (birth.seq + 1) % 256
        for metric in birth.metrics:
            if metric.name:
                self._metrics[metric.name] = metric
        bdseq_metric = self._metrics.get(METRIC_BDSEQ)
        self._last_known_bdseq = bdseq_metric.value if bdseq_metric else None
        self._emsomat_online = True
        logger.info("Emsomat NBIRTH received (bdSeq=%s)", self._last_known_bdseq)
        self._notify_live("on_birth", birth, self._last_known_bdseq)
        self._notify_metrics_changed()

    def _handle_ndata(self, raw: bytes) -> None:
        if self._emsomat_birth is None:
            # Data outside a session cannot be interpreted reliably.
            logger.debug("SparkplugHost: NDATA without NBIRTH ignored, requesting Rebirth")
            self._request_rebirth_limited()
            return
        data = NData.decode(raw, birth=self._emsomat_birth)
        self._check_sequence(data.seq)
        for metric in data.metrics:
            if metric.name:
                self._metrics[metric.name] = metric
        self._notify_live("on_data", data)
        self._notify_metrics_changed()

    def _check_sequence(self, seq: int) -> None:
        """A gap is logged and answered with a Rebirth. It never decides
        whether a value is valid: losses are normal at QoS 0."""
        expected = self._expected_seq
        self._expected_seq = (seq + 1) % 256
        if expected is not None and seq != expected:
            logger.info("SparkplugHost: Sparkplug seq gap (expected %s, got %s)", expected, seq)
            self._request_rebirth_limited()

    def _request_rebirth_limited(self) -> None:
        now = time.monotonic()
        last = self._last_rebirth_request
        if last is not None and now - last < _REBIRTH_MIN_INTERVAL_S:
            return
        self._last_rebirth_request = now
        self.request_rebirth()

    def _notify_metrics_changed(self) -> None:
        if not self._on_metrics_changed:
            return
        try:
            self._on_metrics_changed()
        except Exception:
            logger.debug("SparkplugHost on_metrics_changed callback failed", exc_info=True)

    def _handle_ndeath(self, raw: bytes) -> None:
        ndeath = NDeath.decode(raw)
        received_bdseq = ndeath.bd_seq_metric.value
        if self._last_known_bdseq is not None and received_bdseq != self._last_known_bdseq:
            # stale NDEATH from a previous, already-superseded session - ignore
            # (see Emsomat_Shareomat_MQTT_Vertrag.md Abschnitt 28.4, bdSeq)
            logger.debug(
                "SparkplugHost: ignoring stale NDEATH (bdSeq=%s, current=%s)",
                received_bdseq, self._last_known_bdseq,
            )
            return
        self._forget_emsomat_session()
        logger.warning("Emsomat NDEATH received (bdSeq=%s) - marked offline", received_bdseq)
        self._notify_live("on_death", received_bdseq)
        self._notify_metrics_changed()

    # ------------------------------------------------------------------
    # outgoing: Rebirth request
    # ------------------------------------------------------------------
    def request_rebirth(self) -> None:
        """Publishes NCMD with Node Control/Rebirth=true to Emsomat, per
        Abschnitt 28.6 (required standard mechanic for every Edge Node)."""
        now_ms = get_current_timestamp()
        metric = Metric(timestamp=now_ms, name=METRIC_NODE_REBIRTH, datatype=DataType.BOOLEAN, value=True)
        self._publish_ncmd(NCmd(timestamp=now_ms, metrics=(metric,)), "request_rebirth")

    def publish_coordinator_ncmd(self, metrics: tuple[Metric, ...]) -> None:
        """Sends Coordinator-/Market-Metrics (LEG/DemandForecast,
        LEG/ExportPrice, LEG/FeedInPrice - see sparkplug/coordinator_mapping.py)
        to the local Emsomat Edge Node via NCMD, per
        Emsomat_Shareomat_MQTT_Vertrag.md Abschnitt 9/10. No-op if not
        connected - the next settlement cycle retries; Emsomat's own local
        fallback logic covers the gap until then, never a plain-MQTT
        fallback (Hard Cut, Abschnitt 1/20)."""
        self._publish_ncmd(NCmd(timestamp=get_current_timestamp(), metrics=metrics), "publish_coordinator_ncmd")

    def _publish_ncmd(self, ncmd: NCmd, caller: str) -> None:
        """The check and the publish are one step with respect to stop()."""
        with self._publish_lock:
            if self._stopped or self._client is None or not self._connected.is_set():
                logger.warning("SparkplugHost.%s: not connected, skipped", caller)
                return
            self._client.publish(self._node_topic(MessageType.NCMD), ncmd.encode(include_dtypes=True), qos=_QOS_NON_STATE, retain=False)

    # ------------------------------------------------------------------
    # getters
    # ------------------------------------------------------------------
    @property
    def emsomat_online(self) -> bool:
        return self._emsomat_online

    def get_metric_value(self, name: str):
        metric = self._metrics.get(name)
        return metric.value if metric else None

    def get_metric(self, name: str) -> Optional[Metric]:
        """Full Metric (value + original per-metric timestamp), or None.
        Needed by sparkplug/bridge.py to forward the local Emsomat's own
        state to the WAN Uplink without losing the original timestamp
        (get_metric_value() alone only exposes the value)."""
        return self._metrics.get(name)
