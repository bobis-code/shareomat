# -*- coding: utf-8 -*-
"""
File: shareomat/ha/mqtt_runtime.py

Purpose:
    MQTT runtime: low-level client operations and lifecycle orchestration
    for the Shareomat settlement engine.
    Publishes billing results, handles status topics, listens for command
    triggers, and manages daemon mode and graceful shutdown.

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
        starting  — published in on_connect, immediately after broker CONNACK
        ok        — published after every successful settlement cycle
        error     — published when a settlement cycle raises an exception

    Retained topics:
        shareomat/status                      — engine state
        shareomat/last_run                    — last run timestamp
        shareomat/billing/{participant_id}/*  — per-participant billing values
        homeassistant/sensor/*/config        — HA Discovery (always required)

    NOT retained:
        shareomat/cmd/run_once  — command (subscribed, never published here)
        shareomat/auto_scan/set — switch command topic

    Incoming retained messages on command topics are silently discarded.

    Reconnect behaviour:
        reconnect_delay_set() configures exponential backoff (1s–120s).
        Subscriptions are tracked in client userdata and re-applied in
        on_connect so they survive broker restarts.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import TYPE_CHECKING, Callable

from shareomat.core.leg_config import MqttConfig, LegConfig
from shareomat.leg_const import (
    MQTT_STATUS_ERROR,
    MQTT_STATUS_OFFLINE,
    MQTT_STATUS_OK,
    MQTT_STATUS_STARTING,
)
from shareomat.models.invoice import BillingRecord
from shareomat.ha.mqtt_entities import _topic_safe

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

# ── Module-level watcher reference (wired up from main.py) ───────────────────

_watcher_ref = None  # type: ignore[assignment]


def register_watcher(watcher) -> None:
    """Register a WatcherThread so the auto_scan MQTT switch can control it."""
    global _watcher_ref
    _watcher_ref = watcher


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
        if rc == 0:
            logger.info("MQTT connected to %s:%d", config.broker, config.port)
            client.publish(status_topic, MQTT_STATUS_STARTING, qos=config.qos, retain=True)
            # Re-subscribe to all tracked topics (covers initial connect AND reconnect)
            for topic, qos_val in (userdata or {}).get("subscriptions", []):
                client.subscribe(topic, qos_val)
            connected.set()
        else:
            logger.error("MQTT connection refused: rc=%d", rc)

    def on_disconnect(client, userdata, rc: int) -> None:
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
    client.publish(
        f"{config.topic_prefix}/status",
        status,
        qos=config.qos,
        retain=True,
    )
    logger.debug("MQTT status -> %s", status)


def publish_billing(
    client: "_mqtt_type.Client",
    records: list[BillingRecord],
    config: MqttConfig,
) -> None:
    """Publish per-participant billing results as individual retained MQTT values."""
    prefix = config.topic_prefix
    qos = config.qos
    retain = config.retain

    for rec in records:
        pid_safe = _topic_safe(rec.participant_id)
        base = f"{prefix}/billing/{pid_safe}"

        fields = {
            "label": rec.label,
            "meter_ids": ";".join(rec.meter_ids),
            "total_import_kwh": round(rec.total_import_kwh, 4),
            "local_share_kwh": round(rec.local_received_kwh, 4),
            "grid_import_kwh": round(rec.grid_import_kwh, 4),
            "local_cost_chf": round(rec.local_cost_chf, 4),
            "grid_cost_chf": round(rec.grid_cost_chf, 4),
            "total_cost_chf": round(rec.total_cost_chf, 4),
            "period_start": rec.period_start.isoformat(),
            "period_end": rec.period_end.isoformat(),
        }

        for key, value in fields.items():
            client.publish(f"{base}/{key}", str(value), qos=qos, retain=retain)

        logger.debug("MQTT billing published for %s", rec.participant_id)

    logger.info("MQTT billing published for %d participant(s)", len(records))


def publish_system_state(
    client: "_mqtt_type.Client",
    config: MqttConfig,
    *,
    status: str = "",
    last_run: str = "",
    inbox_count: int | None = None,
    report_count: int | None = None,
    last_error: str = "",
) -> None:
    """Publish any combination of engine system state topics (all kwargs optional)."""
    prefix = config.topic_prefix
    qos = config.qos

    if status:
        client.publish(f"{prefix}/status", status, qos=qos, retain=True)
    if last_run:
        client.publish(f"{prefix}/last_run", last_run, qos=qos, retain=True)
    if inbox_count is not None:
        client.publish(f"{prefix}/inbox_count", str(inbox_count), qos=qos, retain=True)
    if report_count is not None:
        client.publish(f"{prefix}/report_count", str(report_count), qos=qos, retain=True)
    # last_error is always published (empty string clears a previous error)
    client.publish(f"{prefix}/last_error", last_error, qos=qos, retain=True)
    logger.debug("MQTT system state published (status=%s)", status or "-")


def setup_command_subscription(
    client: "_mqtt_type.Client",
    on_run: Callable[[], None],
    config: MqttConfig,
) -> None:
    """Register the run_once subscription on the client (non-blocking)."""
    cmd_topic = f"{config.topic_prefix}/cmd/run_once"
    auto_scan_topic = f"{config.topic_prefix}/auto_scan/set"

    def on_message(client, userdata, message) -> None:
        topic = message.topic
        if message.retain:
            logger.debug("Discarding retained message on %s", topic)
            return
        if topic == cmd_topic:
            logger.info("Command received on %s — triggering settlement run", topic)
            try:
                on_run()
            except Exception as exc:
                logger.error("Unhandled error in command-triggered run: %s", exc)
        elif topic == auto_scan_topic:
            payload = message.payload.decode("utf-8", errors="ignore").strip()
            logger.info("auto_scan/set received: %s", payload)
            if _watcher_ref is not None:
                if payload == "ON":
                    _watcher_ref.resume()
                    client.publish(
                        f"{config.topic_prefix}/auto_scan/state", "ON",
                        qos=config.qos, retain=True,
                    )
                elif payload == "OFF":
                    _watcher_ref.pause()
                    client.publish(
                        f"{config.topic_prefix}/auto_scan/state", "OFF",
                        qos=config.qos, retain=True,
                    )
            else:
                logger.debug("auto_scan/set received but no watcher registered")

    client.on_message = on_message

    # Register both topics in userdata so on_connect re-subscribes after reconnect
    userdata = client.user_data_get() or {}
    subs = userdata.get("subscriptions", [])
    for topic in (cmd_topic, auto_scan_topic):
        if (topic, config.qos) not in subs:
            subs.append((topic, config.qos))
    userdata["subscriptions"] = subs
    client.user_data_set(userdata)

    client.subscribe(cmd_topic, qos=config.qos)
    client.subscribe(auto_scan_topic, qos=config.qos)
    logger.info("Command subscription active: %s", cmd_topic)


def start_command_listener(
    client: "_mqtt_type.Client",
    on_run: Callable[[], None],
    config: MqttConfig,
) -> None:
    """Subscribe to run_once and block until KeyboardInterrupt."""
    setup_command_subscription(client, on_run, config)
    logger.info(
        "Daemon mode active — listening for commands on: %s/cmd/run_once",
        config.topic_prefix,
    )
    import time
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("MQTT command listener stopped")
    finally:
        disconnect_client(client)


# ── Lifecycle orchestration ───────────────────────────────────────────────────


def setup_mqtt(config: LegConfig) -> object | None:
    """Connect to the MQTT broker when enabled in config, or return None."""
    if not config.mqtt.enabled:
        return None
    client = create_client(config.mqtt)
    if client is None:
        logger.warning("MQTT requested but client could not connect — running without MQTT")
    return client


def should_run_daemon(config: LegConfig, mqtt_client: object | None) -> bool:
    """Return True when at least one daemon service is configured."""
    return (
        (mqtt_client is not None and config.mqtt.command_topic_enabled)
        or bool(config.processing.cron_schedule)
        or config.processing.auto_scan_enabled
    )


def run_mqtt_daemon(
    config: LegConfig,
    mqtt_client: object,
    on_run: Callable[[], None],
) -> None:
    """Enter daemon mode: block and wait for run commands from the MQTT broker."""
    logger.info(
        "Daemon mode — publish any payload to %s/cmd/run_once to trigger a run",
        config.mqtt.topic_prefix,
    )
    start_command_listener(mqtt_client, on_run=on_run, config=config.mqtt)


def shutdown_mqtt(mqtt_client: object | None) -> None:
    """Gracefully disconnect from the MQTT broker."""
    if mqtt_client is None:
        return
    disconnect_client(mqtt_client)
