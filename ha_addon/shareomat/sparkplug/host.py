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
        EdgeNodeAdapter.async_stop()) and disconnects."""
        if self._client is None:
            return
        if self._connected.is_set():
            try:
                self._client.publish(self._state_topic, State(online=False).encode(), qos=_QOS_STATE, retain=True)
            except Exception:
                logger.exception("SparkplugHost: failed publishing graceful STATE offline")
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass
        self._connected.clear()

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

    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic
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
        for metric in birth.metrics:
            if metric.name:
                self._metrics[metric.name] = metric
        bdseq_metric = self._metrics.get(METRIC_BDSEQ)
        self._last_known_bdseq = bdseq_metric.value if bdseq_metric else None
        self._emsomat_online = True
        logger.info("Emsomat NBIRTH received (bdSeq=%s)", self._last_known_bdseq)
        self._notify_metrics_changed()

    def _handle_ndata(self, raw: bytes) -> None:
        data = NData.decode(raw, birth=self._emsomat_birth)
        for metric in data.metrics:
            if metric.name:
                self._metrics[metric.name] = metric
        self._notify_metrics_changed()

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
        self._emsomat_online = False
        logger.warning("Emsomat NDEATH received (bdSeq=%s) - marked offline", received_bdseq)

    # ------------------------------------------------------------------
    # outgoing: Rebirth request
    # ------------------------------------------------------------------
    def request_rebirth(self) -> None:
        """Publishes NCMD with Node Control/Rebirth=true to Emsomat, per
        Abschnitt 28.6 (required standard mechanic for every Edge Node)."""
        if self._client is None or not self._connected.is_set():
            logger.warning("SparkplugHost.request_rebirth: not connected, skipped")
            return
        now_ms = get_current_timestamp()
        metric = Metric(timestamp=now_ms, name=METRIC_NODE_REBIRTH, datatype=DataType.BOOLEAN, value=True)
        ncmd = NCmd(timestamp=now_ms, metrics=(metric,))
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
