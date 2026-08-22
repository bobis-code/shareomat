# -*- coding: utf-8 -*-
"""
File: shareomat/sparkplug/wan_downlink.py

WAN Downlink - Shareomat's Sparkplug Host-Application-style role on the
CENTRAL (cross-house) broker: subscribes broadly within its own LEG group to
see all other houses' participants, and feeds them into the local
ParticipantRelay so the local Emsomat sees them exactly as it already does
for any other participant (no new consumption mechanism on Emsomat's side).
See docs/Architektur/Shareomat_CrossHouse_Sparkplug_Vertrag.md Abschnitt 2.2.

Security/isolation properties implemented here (defense in depth, on top of
the broker ACL which is the actual enforcement layer per Abschnitt 6.3):
- rejects any message whose topic group_id != our own configured group_id
  (shouldn't happen given we only subscribe to our own group, but explicit)
- ignores messages whose edge_node_id equals our OWN WAN Uplink's
  edge_node_id (self-echo/loop prevention - the broker would otherwise
  deliver our own Uplink's publishes back to us as if they were "remote")
- NCMD/DCMD are not subscribed to at all (Abschnitt 5, forbidden on WAN)
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING, Optional

from shareomat.config import WanConfig

from .participant_relay import ParticipantRelay
from .vendor.enums import MessageType
from .vendor.metric import Metric
from .vendor.payload import DBirth, DData, DDeath, NDeath
from .vendor.topic import Topic

if TYPE_CHECKING:
    import paho.mqtt.client as _mqtt_type

logger = logging.getLogger(__name__)

METRIC_EXPORT_POWER_NOW = "Market/ExportPowerNow"
METRIC_IMPORT_POWER_NOW = "Market/ImportPowerNow"

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


class WanDownlink:
    """`client` injectable for tests, same convention as the other Sparkplug
    transport classes in this package."""

    def __init__(
        self,
        wan_config: WanConfig,
        local_relay: ParticipantRelay,
        *,
        client_id: str,
        client: Optional["_mqtt_type.Client"] = None,
    ):
        self._wan_config = wan_config
        self._local_relay = local_relay
        self._client_id = client_id
        self._client = client
        self._connected = threading.Event()
        # own_edge_node_id -> the WAN Uplink identity, for self-echo rejection
        self._own_edge_node_id = wan_config.edge_node_id
        # remote edge_node_id -> set of device_ids currently known from it
        # (for cascading removal on that remote house's NDEATH)
        self._remote_devices: dict[str, set[str]] = {}
        # (edge_node_id, device_id) -> last DBirth, for DDATA alias resolution
        self._remote_births: dict[tuple[str, str], DBirth] = {}

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self, *, timeout: float = 5.0) -> bool:
        if not self._wan_config.enabled:
            return False
        if self._client is None:
            if not _PAHO_AVAILABLE:
                logger.error("paho-mqtt not installed - cannot start WanDownlink")
                return False
            self._client = _make_client(self._client_id)

        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message
        if self._wan_config.username:
            self._client.username_pw_set(self._wan_config.username, self._wan_config.password or None)
        if self._wan_config.tls_enabled:
            self._client.tls_set(ca_certs=self._wan_config.tls_ca_cert or None)
        self._client.reconnect_delay_set(min_delay=1, max_delay=120)

        try:
            self._client.connect(self._wan_config.broker, self._wan_config.port, keepalive=60)
        except Exception as exc:
            logger.error("WanDownlink MQTT connect failed: %s", exc)
            return False
        self._client.loop_start()

        if not self._connected.wait(timeout=timeout):
            logger.error("WanDownlink connection timeout - central broker unreachable")
            self._client.loop_stop()
            return False
        return True

    def stop(self) -> None:
        if self._client is None:
            return
        try:
            self._client.loop_stop()
            self._client.disconnect()
        except Exception:
            pass
        self._connected.clear()

    def _on_connect(self, client, userdata, flags, rc=0, properties=None) -> None:
        ok = (int(rc) == 0) if not hasattr(rc, "value") else (rc.value == 0)
        if not ok:
            logger.error("WanDownlink connect failed: rc=%s", rc)
            return
        self._connected.set()
        group_prefix = f"spBv1.0/{self._wan_config.group_id}"
        # Wildcard within our own LEG group only - never subscribes across
        # groups (Abschnitt 6.3, "Subscribe nur innerhalb der eigenen LEG").
        # NCMD/DCMD are deliberately not subscribed to at all (Abschnitt 5).
        for message_type in (MessageType.DBIRTH, MessageType.DDATA, MessageType.DDEATH, MessageType.NDEATH):
            client.subscribe(f"{group_prefix}/{message_type}/#", qos=0)
        logger.info("WanDownlink connected, subscribed to group_id=%s", self._wan_config.group_id)

    def _on_message(self, client, userdata, msg) -> None:
        try:
            parsed = Topic.from_str(msg.topic)
        except Exception:
            logger.warning("WanDownlink: unparsable topic %s", msg.topic, exc_info=True)
            return

        # Defense in depth: reject anything not in our own LEG group (the
        # broker ACL is the real enforcement, this is a second check).
        if parsed.group_id != self._wan_config.group_id:
            logger.warning(
                "WanDownlink: rejecting message for foreign group_id=%s (expected %s) - "
                "broker ACL should have prevented this from arriving at all",
                parsed.group_id, self._wan_config.group_id,
            )
            return

        # Loop/self-echo prevention: never treat our own Uplink's publishes
        # as if they were a remote participant (Abschnitt 3).
        if parsed.edge_node_id == self._own_edge_node_id:
            return

        try:
            if parsed.message_type == MessageType.DBIRTH:
                self._handle_dbirth(parsed, msg.payload)
            elif parsed.message_type == MessageType.DDATA:
                self._handle_ddata(parsed, msg.payload)
            elif parsed.message_type == MessageType.DDEATH:
                self._handle_ddeath(parsed)
            elif parsed.message_type == MessageType.NDEATH:
                self._handle_ndeath(parsed)
        except Exception:
            logger.warning("WanDownlink: failed handling message on %s", msg.topic, exc_info=True)

    # ------------------------------------------------------------------
    # remote lifecycle -> local ParticipantRelay (pure logic, unit-testable)
    # ------------------------------------------------------------------
    def _extract_power(self, metrics) -> Optional[tuple[float, float, int]]:
        by_name = {m.name: m for m in metrics if m.name}
        export_m = by_name.get(METRIC_EXPORT_POWER_NOW)
        import_m = by_name.get(METRIC_IMPORT_POWER_NOW)
        if export_m is None or import_m is None:
            return None
        ts = export_m.timestamp if export_m.timestamp is not None else None
        return float(export_m.value or 0.0), float(import_m.value or 0.0), ts

    def _handle_dbirth(self, parsed: Topic, raw: bytes) -> None:
        birth = DBirth.decode(raw)
        self._remote_births[(parsed.edge_node_id, parsed.device_id)] = birth
        self._remote_devices.setdefault(parsed.edge_node_id, set()).add(parsed.device_id)

        power = self._extract_power(birth.metrics)
        if power is None:
            return
        export_w, import_w, ts_ms = power
        if parsed.device_id in self._local_relay.known_participants:
            self._local_relay.update_participant(parsed.device_id, export_w, import_w, timestamp_ms=ts_ms)
        else:
            self._local_relay.register_participant(parsed.device_id, export_w, import_w, timestamp_ms=ts_ms)

    def _handle_ddata(self, parsed: Topic, raw: bytes) -> None:
        birth = self._remote_births.get((parsed.edge_node_id, parsed.device_id))
        data = DData.decode(raw, birth=birth)

        power = self._extract_power(data.metrics)
        if power is None:
            return
        export_w, import_w, ts_ms = power
        if parsed.device_id not in self._local_relay.known_participants:
            logger.warning(
                "WanDownlink: DDATA for unknown participant %s (no prior DBIRTH seen) - ignored",
                parsed.device_id,
            )
            return
        self._local_relay.update_participant(parsed.device_id, export_w, import_w, timestamp_ms=ts_ms)

    def _handle_ddeath(self, parsed: Topic) -> None:
        self._remote_devices.get(parsed.edge_node_id, set()).discard(parsed.device_id)
        self._remote_births.pop((parsed.edge_node_id, parsed.device_id), None)
        self._local_relay.deregister_participant(parsed.device_id)

    def _handle_ndeath(self, parsed: Topic) -> None:
        """NDEATH implies death of ALL Devices previously birthed under that
        remote Edge Node (Sparkplug semantics - a crashed remote house can't
        send individual DDEATHs, see
        Shareomat_CrossHouse_Sparkplug_Vertrag.md Abschnitt 5)."""
        device_ids = self._remote_devices.pop(parsed.edge_node_id, set())
        for device_id in device_ids:
            self._remote_births.pop((parsed.edge_node_id, device_id), None)
            self._local_relay.deregister_participant(device_id)
        if device_ids:
            logger.warning(
                "WanDownlink: remote edge_node_id=%s died - removed %d participant(s) locally",
                parsed.edge_node_id, len(device_ids),
            )

    @property
    def known_remote_edge_nodes(self) -> tuple[str, ...]:
        return tuple(self._remote_devices.keys())
