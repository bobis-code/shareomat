# -*- coding: utf-8 -*-
"""
File: shareomat/sparkplug/participant_relay.py

ParticipantRelay - Shareomat's own, separate Sparkplug Edge Node identity
(distinct from its Primary Host Application role in host.py, see
docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md Abschnitt 6: "Shareomat =
Host + zusaetzliche eigene Edge-Node-Identitaet"). Publishes known LEG
participants as Sparkplug Devices (PARTICIPANT_<id>, ExportPower/
ImportPower) via DBIRTH/DDATA/DDEATH - Emsomat consumes these and maps them
onto its own NodeMarketState.

Deliberately empty by default: which participants exist and what their live
power is depends on the not-yet-specified Cross-House/Coordinator layer
(Shareomat <-> Internet/Relay <-> fremder Shareomat) - out of scope here.
This class only provides the mechanism (register/update/deregister); no
participant is registered unless a real caller does so.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Optional

from shareomat.config import MqttConfig, SparkplugConfig

from .vendor.datatype import DataType
from .vendor.enums import MessageType
from .vendor.metric import Metric
from .vendor.payload import DBirth, DData, DDeath, NBirth, NCmd, NDeath
from .vendor.time_utils import get_current_timestamp
from .vendor.topic import Topic

if TYPE_CHECKING:
    import paho.mqtt.client as _mqtt_type

logger = logging.getLogger(__name__)

METRIC_BDSEQ = "bdSeq"
METRIC_EXPORT_POWER_NOW = "Market/ExportPowerNow"
METRIC_IMPORT_POWER_NOW = "Market/ImportPowerNow"
METRIC_NODE_REBIRTH = "Node Control/Rebirth"

_QOS_NON_STATE = 0

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
    if _CB_API_V1 is not None:
        return mqtt.Client(callback_api_version=_CB_API_V1, client_id=client_id)
    return mqtt.Client(client_id=client_id)


class ParticipantRelay:
    """`client` injectable for tests, same convention as SparkplugHost.

    `enable_ncmd` controls whether this Edge Node subscribes to its own NCMD
    topic and handles Node Control/Rebirth - required locally (Abschnitt
    28.6, every Edge Node MUST implement Rebirth), but the Cross-House
    Sparkplug contract (Shareomat_CrossHouse_Sparkplug_Vertrag.md Abschnitt
    5) forbids NCMD/DCMD on the WAN side entirely. The WAN Uplink
    (wan_uplink factory below) constructs this class with
    `enable_ncmd=False` - same class, same Birth/Death/DBIRTH/DDATA
    mechanics, just without the WAN-forbidden command channel."""

    def __init__(
        self,
        sp_config: SparkplugConfig,
        mqtt_config: MqttConfig,
        *,
        client: Optional["_mqtt_type.Client"] = None,
        enable_ncmd: bool = True,
    ):
        self._sp_config = sp_config
        self._mqtt_config = mqtt_config
        self._client = client
        self._enable_ncmd = enable_ncmd
        self._connected = threading.Event()
        self._bdseq = 0
        self._seq = 0
        self._participants: dict[str, tuple[float, float]] = {}

    @property
    def edge_node_id(self) -> str:
        return self._sp_config.participant_edge_node_id

    # ------------------------------------------------------------------
    # topics
    # ------------------------------------------------------------------
    def _node_topic(self, message_type: MessageType) -> str:
        return str(Topic(group_id=self._sp_config.group_id, message_type=message_type, edge_node_id=self.edge_node_id))

    def _device_topic(self, message_type: MessageType, device_id: str) -> str:
        return str(Topic(
            group_id=self._sp_config.group_id, message_type=message_type,
            edge_node_id=self.edge_node_id, device_id=device_id,
        ))

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, *, timeout: float = 5.0) -> bool:
        if not self._sp_config.enabled:
            return False
        if self._client is None:
            if not _PAHO_AVAILABLE:
                logger.error("paho-mqtt not installed - cannot start ParticipantRelay")
                return False
            self._client = _make_client(f"{self._mqtt_config.client_id}-sparkplug-relay")

        self._bdseq += 1
        bdseq_metric = Metric(timestamp=None, name=METRIC_BDSEQ, datatype=DataType.INT64, value=self._bdseq)
        ndeath = NDeath(timestamp=None, bd_seq_metric=bdseq_metric)
        self._client.will_set(self._node_topic(MessageType.NDEATH), ndeath.encode(), qos=_QOS_NON_STATE, retain=False)

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
            logger.error("ParticipantRelay MQTT connect failed: %s", exc)
            return False
        self._client.loop_start()

        if not self._connected.wait(timeout=timeout):
            logger.error("ParticipantRelay connection timeout - broker unreachable")
            self._client.loop_stop()
            return False
        return True

    def stop(self) -> None:
        if self._client is None:
            return
        if self._connected.is_set():
            try:
                bdseq_metric = Metric(timestamp=None, name=METRIC_BDSEQ, datatype=DataType.INT64, value=self._bdseq)
                ndeath = NDeath(timestamp=None, bd_seq_metric=bdseq_metric)
                self._client.publish(self._node_topic(MessageType.NDEATH), ndeath.encode(), qos=_QOS_NON_STATE, retain=False)
            except Exception:
                logger.exception("ParticipantRelay: failed publishing graceful NDEATH")
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass
        self._connected.clear()

    def _on_connect(self, client, userdata, flags, rc=0, properties=None) -> None:
        ok = (int(rc) == 0) if not hasattr(rc, "value") else (rc.value == 0)
        if not ok:
            logger.error("ParticipantRelay connect failed: rc=%s", rc)
            return
        self._connected.set()
        if self._enable_ncmd:
            client.subscribe(self._node_topic(MessageType.NCMD), qos=_QOS_NON_STATE)
        self._publish_birth()
        logger.info("ParticipantRelay connected as edge_node_id=%s (ncmd=%s)", self.edge_node_id, self._enable_ncmd)

    def _on_message(self, client, userdata, msg) -> None:
        if not self._enable_ncmd:
            return
        if msg.topic != self._node_topic(MessageType.NCMD):
            return
        try:
            ncmd = NCmd.decode(msg.payload)
        except Exception:
            logger.warning("ParticipantRelay: invalid NCMD payload", exc_info=True)
            return
        for metric in ncmd.metrics:
            if metric.name == METRIC_NODE_REBIRTH and metric.value:
                logger.info("ParticipantRelay: Node Control/Rebirth requested")
                self._publish_birth()

    def _publish_birth(self) -> None:
        """NBIRTH (kein eigener Node-Metric ausser bdSeq - der ganze fachliche
        Inhalt sitzt auf Device-Ebene) + DBIRTH je registriertem Teilnehmer,
        Abschnitt 28.7 (NBIRTH immer zuerst)."""
        self._seq = 0
        now_ms = get_current_timestamp()
        bdseq_metric = Metric(timestamp=None, name=METRIC_BDSEQ, datatype=DataType.INT64, value=self._bdseq)
        rebirth_metric = Metric(timestamp=None, name=METRIC_NODE_REBIRTH, datatype=DataType.BOOLEAN, value=False)
        birth = NBirth(timestamp=now_ms, seq=self._seq, metrics=(bdseq_metric, rebirth_metric))
        self._client.publish(self._node_topic(MessageType.NBIRTH), birth.encode(include_dtypes=True), qos=_QOS_NON_STATE, retain=False)
        self._seq = 1

        for device_id, (export_w, import_w) in self._participants.items():
            self._publish_dbirth(device_id, export_w, import_w)

    def _publish_dbirth(self, device_id: str, export_now_w: float, import_now_w: float, *, timestamp_ms: Optional[int] = None) -> None:
        metric_ts = timestamp_ms if timestamp_ms is not None else get_current_timestamp()
        metrics = (
            Metric(timestamp=metric_ts, name=METRIC_EXPORT_POWER_NOW, datatype=DataType.DOUBLE, value=float(export_now_w)),
            Metric(timestamp=metric_ts, name=METRIC_IMPORT_POWER_NOW, datatype=DataType.DOUBLE, value=float(import_now_w)),
        )
        birth = DBirth(timestamp=get_current_timestamp(), seq=self._seq, metrics=metrics)
        self._client.publish(self._device_topic(MessageType.DBIRTH, device_id), birth.encode(include_dtypes=True), qos=_QOS_NON_STATE, retain=False)
        self._seq = (self._seq + 1) % 256

    # ------------------------------------------------------------------
    # public API - register/update/deregister participants
    # ------------------------------------------------------------------
    def register_participant(
        self, device_id: str, export_now_w: float, import_now_w: float, *, timestamp_ms: Optional[int] = None,
    ) -> None:
        """Publishes DBIRTH for a new participant. If already registered,
        updates values via DDATA instead (idempotent, no duplicate birth).

        `timestamp_ms` is the Metric-level timestamp (when the value was
        ORIGINALLY measured) - defaults to now for genuinely local
        participants, but WanDownlink passes through the original remote
        Metric timestamp when relaying (Shareomat_CrossHouse_Sparkplug_Vertrag.md
        Abschnitt 5, "urspruengliche Metric-Timestamps beim Weiterreichen
        erhalten"). This is the Payload timestamp (get_current_timestamp())
        vs. Metric timestamp distinction from Emsomat_Shareomat_MQTT_Vertrag.md
        Abschnitt 27 applied across the WAN hop too."""
        if device_id in self._participants:
            self.update_participant(device_id, export_now_w, import_now_w, timestamp_ms=timestamp_ms)
            return
        self._participants[device_id] = (export_now_w, import_now_w)
        if self._connected.is_set():
            self._publish_dbirth(device_id, export_now_w, import_now_w, timestamp_ms=timestamp_ms)

    def update_participant(
        self, device_id: str, export_now_w: float, import_now_w: float, *, timestamp_ms: Optional[int] = None,
    ) -> None:
        """Publishes DDATA for an already-registered participant. See
        register_participant() for `timestamp_ms`."""
        if device_id not in self._participants:
            raise ValueError(f"participant {device_id!r} is not registered - call register_participant() first")
        self._participants[device_id] = (export_now_w, import_now_w)
        if not self._connected.is_set():
            return
        metric_ts = timestamp_ms if timestamp_ms is not None else get_current_timestamp()
        metrics = (
            Metric(timestamp=metric_ts, name=METRIC_EXPORT_POWER_NOW, datatype=DataType.DOUBLE, value=float(export_now_w)),
            Metric(timestamp=metric_ts, name=METRIC_IMPORT_POWER_NOW, datatype=DataType.DOUBLE, value=float(import_now_w)),
        )
        data = DData(timestamp=get_current_timestamp(), seq=self._seq, metrics=metrics)
        self._client.publish(self._device_topic(MessageType.DDATA, device_id), data.encode(include_dtypes=True), qos=_QOS_NON_STATE, retain=False)
        self._seq = (self._seq + 1) % 256

    def deregister_participant(self, device_id: str) -> None:
        """Publishes DDEATH and removes the participant from the registry."""
        if device_id not in self._participants:
            return
        del self._participants[device_id]
        if not self._connected.is_set():
            return
        now_ms = get_current_timestamp()
        death = DDeath(timestamp=now_ms, seq=self._seq)
        self._client.publish(self._device_topic(MessageType.DDEATH, device_id), death.encode(), qos=_QOS_NON_STATE, retain=False)
        self._seq = (self._seq + 1) % 256

    @property
    def known_participants(self) -> tuple[str, ...]:
        return tuple(self._participants.keys())
