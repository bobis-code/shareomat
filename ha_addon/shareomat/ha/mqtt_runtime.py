# -*- coding: utf-8 -*-
"""
File: shareomat/ha/mqtt_runtime.py

Purpose:
    MQTT runtime: low-level client operations and lifecycle for the
    Shareomat settlement engine: connect, engine status, graceful shutdown.
    The billing values, system topics and commands of the former CSV run
    (run_once, auto_scan) were removed in 0.6.34 (ADR-0012); their HA
    entities are retired by mqtt_discovery.retire_entities().

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    paho-mqtt is a soft dependency: if not installed, MQTT is silently
    unavailable. When mqtt.enabled = false in config, this module is
    never called and the engine behaves exactly as without MQTT.

    Home Assistant integration uses pure MQTT Discovery — no HA Python
    library or API dependency of any kind.

    paho-mqtt v1.x and v2.x are both supported via a compatibility shim.

    Status lifecycle on shareomat/status (always retained):
        offline   — Last Will Testament (broker sends on ungraceful disconnect)
        starting  — published in on_connect until Shareomat runs
        ok        — published once Shareomat runs (mark_running), and again
                    after every reconnect (the last status is kept)
        error     — publish_status(..., "error") if a part fails for good

    Retained topics:
        shareomat/status                      — engine state
        shareomat/live/*                      — live values (live_sensors.py)
        homeassistant/*/*/config             — HA Discovery

    Coordinator-/Market-data for Emsomat (day-ahead export price, LEG
    producer feed-in credit, LEG demand forecast) no longer travels over
    plain MQTT here — see shareomat/sparkplug/host.py::SparkplugHost.
    publish_coordinator_ncmd() and shareomat/sparkplug/coordinator_mapping.py
    (ADR-0004 Hard Cut: the former {prefix}/energy_data/* topics have been
    removed without a plain-MQTT replacement).

    Reconnect behaviour:
        reconnect_delay_set() configures exponential backoff (1s–120s).
        Subscriptions are tracked in client userdata and re-applied in
        on_connect so they survive broker restarts.
"""

from __future__ import annotations

import logging
import threading
from typing import TYPE_CHECKING

from shareomat.config import MqttConfig
from shareomat.leg_const import (
    MQTT_STATUS_OFFLINE,
    MQTT_STATUS_OK,
    MQTT_STATUS_STARTING,
)

if TYPE_CHECKING:
    import paho.mqtt.client as _mqtt_type

logger = logging.getLogger(__name__)

# ── paho-mqtt import with graceful degradation ────────────────────────────────

try:
    import paho.mqtt.client as mqtt
    _PAHO_AVAILABLE = True
    try:
        # paho-mqtt >= 2.0 requires an explicit callback API version
        _CB_API_V1 = mqtt.CallbackAPIVersion.VERSION1
    except AttributeError:
        # paho-mqtt < 2.0
        _CB_API_V1 = None
except ImportError:
    _PAHO_AVAILABLE = False
    logger.warning("paho-mqtt not installed — MQTT features are unavailable")

# ── Private helpers ───────────────────────────────────────────────────────────


def _make_client(client_id: str) -> "_mqtt_type.Client":
    """Create a paho MQTT client compatible with both v1 and v2 of the library."""
    if _CB_API_V1 is not None:
        return mqtt.Client(callback_api_version=_CB_API_V1, client_id=client_id)
    return mqtt.Client(client_id=client_id)


# ── Public API ────────────────────────────────────────────────────────────────


def create_client(config: MqttConfig) -> "_mqtt_type.Client | None":
    """Connect to the MQTT broker and return the client, or None if connection fails."""
    if not config.enabled:
        return None
    if not _PAHO_AVAILABLE:
        logger.error("paho-mqtt not installed — cannot enable MQTT")
        return None

    connected = threading.Event()
    status_topic = f"{config.topic_prefix}/status"

    def on_connect(client, userdata, flags, rc: int) -> None:
        """Republish the last engine status ('starting' before the first) and re-apply subscriptions on (re)connect.

        The broker's Will set "offline" on the disconnect; the last status itself is kept in userdata.
        """
        if rc == 0:
            logger.info("MQTT connected to %s:%d", config.broker, config.port)
            status = (userdata or {}).get("status") or MQTT_STATUS_STARTING
            client.publish(status_topic, status, qos=config.qos, retain=True)
            # Re-subscribe to all tracked topics (covers initial connect AND reconnect)
            for topic, qos_val in (userdata or {}).get("subscriptions", []):
                client.subscribe(topic, qos_val)
            connected.set()
        else:
            logger.error("MQTT connection refused: rc=%d", rc)

    def on_disconnect(client, userdata, rc: int) -> None:
        """Log unexpected disconnects (paho reconnects automatically)."""
        if rc != 0:
            logger.warning("MQTT unexpected disconnect (rc=%d) — reconnecting...", rc)

    client = _make_client(config.client_id)
    client.user_data_set({"subscriptions": []})
    client.on_connect = on_connect
    client.on_disconnect = on_disconnect

    if config.username:
        client.username_pw_set(config.username, config.password or None)

    # TLS support
    if config.tls_enabled:
        ca = config.tls_ca_cert if config.tls_ca_cert else None
        client.tls_set(ca_certs=ca)
        logger.info("MQTT TLS enabled (CA: %s)", ca or "system CAs")

    # Last Will: broker publishes this if the client disconnects ungracefully
    client.will_set(status_topic, MQTT_STATUS_OFFLINE, qos=config.qos, retain=True)

    # Exponential reconnect backoff: 1s min, 120s max
    client.reconnect_delay_set(min_delay=1, max_delay=120)

    try:
        client.connect(config.broker, config.port, keepalive=60)
    except Exception as exc:
        logger.error("MQTT connect failed: %s", exc)
        return None

    # Starts the MQTT network loop
    client.loop_start()

    if not connected.wait(timeout=5.0):
        logger.error(
            "MQTT connection timeout — broker unreachable at %s:%d",
            config.broker, config.port,
        )
        client.loop_stop()
        return None

    return client


def _remember_status(client: "_mqtt_type.Client", status: str) -> None:
    """Keep the last engine status, so on_connect sends it again after a reconnect."""
    userdata = client.user_data_get()
    if not isinstance(userdata, dict):
        userdata = {}
    userdata["status"] = status
    client.user_data_set(userdata)


def mark_running(client: "_mqtt_type.Client", config: MqttConfig) -> None:
    """Shareomat is up: status 'ok' from startup on, no longer only after a settlement run (B26, P2a)."""
    publish_status(client, MQTT_STATUS_OK, config)


def disconnect_client(client: "_mqtt_type.Client | None") -> None:
    """Disconnect from the MQTT broker and stop the background network loop."""
    if client is None:
        return
    try:
        client.disconnect()
        client.loop_stop()
    except Exception as exc:
        logger.warning("MQTT disconnect error: %s", exc)


def publish_status(
    client: "_mqtt_type.Client",
    status: str,
    config: MqttConfig,
) -> None:
    """Publish the engine status ('ok', 'error', 'starting') to the MQTT status topic."""
    _remember_status(client, status)
    client.publish(
        f"{config.topic_prefix}/status",
        status,
        qos=config.qos,
        retain=True,
    )
    logger.debug("MQTT status -> %s", status)


# ── Lifecycle orchestration ───────────────────────────────────────────────────


def setup_mqtt(mqtt_config: MqttConfig) -> object | None:
    """Connect to the MQTT broker when enabled in config, or return None."""
    if not mqtt_config.enabled:
        return None
    client = create_client(mqtt_config)
    if client is None:
        logger.warning("MQTT requested but client could not connect — running without MQTT")
    return client


def shutdown_mqtt(mqtt_client: object | None) -> None:
    """Gracefully disconnect from the MQTT broker."""
    if mqtt_client is None:
        return
    disconnect_client(mqtt_client)
