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
- only registered (edge_node_id, device_id) pairs are accepted; everything
  else is dropped (ADR-0007 S-L4). The topic is only an assignment key, not
  an authentication: the broker ACL has to bind a login to its edge_node_id.
- retained, oversized or overlong messages are dropped before decoding
- an invalid value stays invalid (is_null), it never becomes 0 W (S-L1)
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Optional

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
METRIC_POWER_TIME_SOURCE = "Market/PowerTimeSource"

# A participant message holds a handful of metrics; anything bigger is not ours.
MAX_WAN_PAYLOAD_BYTES = 16_384
MAX_WAN_METRICS = 32
_MAX_UNKNOWN_PAIRS = 32
_MAX_TIME_SOURCE_LEN = 32

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


@dataclass(frozen=True)
class _Power:
    export_w: Optional[float]
    import_w: Optional[float]
    timestamp_ms: Optional[int]
    time_source: Optional[str]


def _clean_power(metric: Metric) -> Optional[float]:
    if metric.is_null or metric.value is None or isinstance(metric.value, bool):
        return None
    try:
        value = float(metric.value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


class WanDownlink:
    """`client` injectable for tests, same convention as the other Sparkplug
    transport classes in this package.

    `allowed_source(edge_node_id, device_id)` decides which remote pairs are
    registered. It is deliberately required: there is no accept-everything
    default for data coming from another site."""

    def __init__(
        self,
        wan_config: WanConfig,
        local_relay: ParticipantRelay,
        *,
        client_id: str,
        allowed_source: Callable[[str, str], bool],
        client: Optional["_mqtt_type.Client"] = None,
        live_sink: Optional[object] = None,
    ):
        self._wan_config = wan_config
        self._allowed_source = allowed_source
        self._live_sink = live_sink
        # Recently seen, not registered pairs - for the admin page (bounded).
        self._unknown_pairs: "OrderedDict[tuple[str, str], None]" = OrderedDict()
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
        self._client.on_disconnect = self._on_disconnect
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
        self._notify_live("on_connection", True)

    def _on_disconnect(self, client, userdata, rc=0, properties=None) -> None:
        """Lost the central broker: a remote death may go unseen from now on,
        so no remote value may stay standing (also not at the local Emsomat)."""
        self._connected.clear()
        logger.warning("WanDownlink disconnected from central broker (rc=%s) - remote participants removed", rc)
        for device_ids in self._remote_devices.values():
            for device_id in device_ids:
                self._local_relay.deregister_participant(device_id)
        self._remote_devices.clear()
        self._remote_births.clear()
        self._notify_live("on_connection", False)

    def _notify_live(self, method: str, *args) -> None:
        if self._live_sink is None:
            return
        try:
            getattr(self._live_sink, method)(*args)
        except Exception:
            logger.debug("WanDownlink live sink %s failed", method, exc_info=True)

    def _is_allowed(self, parsed: Topic) -> bool:
        pair = (parsed.edge_node_id, parsed.device_id)
        try:
            allowed = bool(self._allowed_source(*pair))
        except Exception:
            logger.warning("WanDownlink: allowed_source check failed - message dropped", exc_info=True)
            return False
        if allowed:
            return True
        if pair not in self._unknown_pairs:
            logger.warning("WanDownlink: dropped message of unregistered source %r", pair)
            self._unknown_pairs[pair] = None
            while len(self._unknown_pairs) > _MAX_UNKNOWN_PAIRS:
                self._unknown_pairs.popitem(last=False)
        else:
            self._unknown_pairs.move_to_end(pair)
        return False

    @property
    def unknown_pairs(self) -> tuple[tuple[str, str], ...]:
        """Pairs seen on the broker that are not registered as a live source."""
        return tuple(self._unknown_pairs)

    def _on_message(self, client, userdata, msg) -> None:
        if getattr(msg, "retain", False):
            logger.warning("WanDownlink: discarded retained message on %r", msg.topic)
            return
        if len(msg.payload) > MAX_WAN_PAYLOAD_BYTES:
            logger.warning("WanDownlink: discarded oversized message on %r (%d bytes)", msg.topic, len(msg.payload))
            return
        try:
            parsed = Topic.from_str(msg.topic)
        except Exception:
            logger.warning("WanDownlink: unparsable topic %r", msg.topic)
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
    def _extract_power(self, metrics, payload_timestamp: Optional[int] = None) -> Optional[_Power]:
        """Export and import of one message, invalid values as None. Returns
        None if the message does not carry both metrics."""
        if len(metrics) > MAX_WAN_METRICS:
            logger.warning("WanDownlink: message with %d metrics dropped", len(metrics))
            return None
        by_name = {m.name: m for m in metrics if m.name}
        export_m = by_name.get(METRIC_EXPORT_POWER_NOW)
        import_m = by_name.get(METRIC_IMPORT_POWER_NOW)
        if export_m is None or import_m is None:
            return None
        export_w = _clean_power(export_m)
        import_w = _clean_power(import_m)
        ts = export_m.timestamp if export_w is not None else import_m.timestamp
        if ts is None:
            ts = payload_timestamp
        time_source = None
        source_m = by_name.get(METRIC_POWER_TIME_SOURCE)
        if source_m is not None and not source_m.is_null and isinstance(source_m.value, str):
            candidate = source_m.value.strip()
            time_source = candidate if candidate and len(candidate) <= _MAX_TIME_SOURCE_LEN else None
        return _Power(export_w, import_w, ts, time_source)

    def _handle_dbirth(self, parsed: Topic, raw: bytes) -> None:
        if not self._is_allowed(parsed):
            return
        birth = DBirth.decode(raw)
        self._remote_births[(parsed.edge_node_id, parsed.device_id)] = birth
        self._remote_devices.setdefault(parsed.edge_node_id, set()).add(parsed.device_id)
        self._notify_live("on_device_birth", parsed.edge_node_id, parsed.device_id, birth)

        power = self._extract_power(birth.metrics, birth.timestamp)
        if power is None:
            return
        if parsed.device_id in self._local_relay.known_participants:
            self._local_relay.update_participant(parsed.device_id, power.export_w, power.import_w,
                                                 timestamp_ms=power.timestamp_ms, time_source=power.time_source)
        else:
            self._local_relay.register_participant(parsed.device_id, power.export_w, power.import_w,
                                                   timestamp_ms=power.timestamp_ms, time_source=power.time_source)

    def _handle_ddata(self, parsed: Topic, raw: bytes) -> None:
        if not self._is_allowed(parsed):
            return
        birth = self._remote_births.get((parsed.edge_node_id, parsed.device_id))
        if birth is None:
            logger.debug("WanDownlink: DDATA for %r without DBIRTH ignored", parsed.device_id)
            return
        data = DData.decode(raw, birth=birth)
        self._notify_live("on_device_data", parsed.edge_node_id, parsed.device_id, data)

        power = self._extract_power(data.metrics, data.timestamp)
        if power is None:
            return
        if parsed.device_id not in self._local_relay.known_participants:
            logger.warning(
                "WanDownlink: DDATA for unknown participant %r (no prior DBIRTH seen) - ignored",
                parsed.device_id,
            )
            return
        self._local_relay.update_participant(parsed.device_id, power.export_w, power.import_w,
                                             timestamp_ms=power.timestamp_ms, time_source=power.time_source)

    def _handle_ddeath(self, parsed: Topic) -> None:
        if not self._is_allowed(parsed):
            return
        self._remote_devices.get(parsed.edge_node_id, set()).discard(parsed.device_id)
        self._remote_births.pop((parsed.edge_node_id, parsed.device_id), None)
        self._local_relay.deregister_participant(parsed.device_id)
        self._notify_live("on_device_death", parsed.edge_node_id, parsed.device_id)

    def _handle_ndeath(self, parsed: Topic) -> None:
        """NDEATH implies death of ALL Devices previously birthed under that
        remote Edge Node (Sparkplug semantics - a crashed remote house can't
        send individual DDEATHs, see
        Shareomat_CrossHouse_Sparkplug_Vertrag.md Abschnitt 5)."""
        device_ids = self._remote_devices.pop(parsed.edge_node_id, set())
        for device_id in device_ids:
            self._remote_births.pop((parsed.edge_node_id, device_id), None)
            self._local_relay.deregister_participant(device_id)
            self._notify_live("on_device_death", parsed.edge_node_id, device_id)
        if device_ids:
            logger.warning(
                "WanDownlink: remote edge_node_id=%s died - removed %d participant(s) locally",
                parsed.edge_node_id, len(device_ids),
            )

    @property
    def known_remote_edge_nodes(self) -> tuple[str, ...]:
        return tuple(self._remote_devices.keys())
