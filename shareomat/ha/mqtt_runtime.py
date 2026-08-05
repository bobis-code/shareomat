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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from shareomat.config import MqttConfig
from shareomat.leg_const import (
    MQTT_STATUS_ERROR,
    MQTT_STATUS_OFFLINE,
    MQTT_STATUS_OK,
    MQTT_STATUS_STARTING,
    SLOT_MINUTES,
)
from shareomat.models.billing import BillingRecord
from shareomat.ha.entities import _topic_safe
from shareomat.database.consumption_forecasts import list_consumption_forecasts
from shareomat.database.price_forecasts import list_price_forecasts
from shareomat.database.settlement_history import aggregate_leg_local_grid, list_settlement_history

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
        """Publish 'starting' status and re-apply tracked subscriptions on (re)connect."""
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


def publish_energy_data_snapshot(client: "_mqtt_type.Client", config: MqttConfig, db_path: Path) -> None:
    """Publish short-horizon prices and recent local/grid history for external MQTT consumers (e.g. Emsomat).

    Deliberately generic topic namespace (not tied to one named consumer) —
    any subscriber can read these. Only short-horizon data is published;
    long-term history stays queryable in SQLite only (see
    shareomat.database.settlement_history / meter_readings).
    """
    now = datetime.now(timezone.utc)
    prefix = config.topic_prefix
    qos = config.qos
    envelope_base = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=config.energy_data_ttl_seconds)).isoformat(),
        "source": "shareomat",
        "quality": "ok",
    }

    horizon_start = now.date()
    horizon_end = horizon_start + timedelta(days=7)
    forecasts = [
        f for f in list_price_forecasts(db_path, limit=30)
        if f.period_end is None or (f.period_start <= horizon_end and f.period_end >= horizon_start)
    ]
    price_series = [
        {
            "period_start": f.period_start.isoformat(),
            "period_end": f.period_end.isoformat() if f.period_end else None,
            "price_chf_kwh": str(f.forecast_price_chf_kwh),
            "kind": "forecast",
        }
        for f in forecasts
    ]
    prices_payload = {**envelope_base, "data": {"currency": "CHF", "series": price_series}}
    client.publish(f"{prefix}/energy_data/prices", json.dumps(prices_payload), qos=qos, retain=True)

    history_start = now - timedelta(days=7)
    leg_totals = aggregate_leg_local_grid(db_path, history_start, now)
    leg_series = [
        {"period_start": t["period_start"], "period_end": t["period_end"],
         "local_kwh": t["local_kwh"], "grid_kwh": t["grid_kwh"], "kind": "recent_actual"}
        for t in leg_totals
    ]
    participants_payload: dict[str, list[dict]] = {}
    for rec in list_settlement_history(db_path, start=history_start, end=now):
        participants_payload.setdefault(rec.participant_id, []).append({
            "period_start": rec.period_start.isoformat(),
            "period_end": rec.period_end.isoformat(),
            "local_kwh": rec.local_received_kwh,
            "grid_kwh": rec.grid_import_kwh,
            "kind": "recent_actual",
        })
    local_grid_payload = {
        **envelope_base,
        "data": {"leg": {"series": leg_series}, "participants": participants_payload},
    }
    client.publish(f"{prefix}/energy_data/local_grid", json.dumps(local_grid_payload), qos=qos, retain=True)

    logger.info(
        "MQTT energy_data snapshot published (prices=%d, leg_periods=%d, participants=%d)",
        len(price_series), len(leg_series), len(participants_payload),
    )


def publish_demand_forecast(client: "_mqtt_type.Client", config: MqttConfig, db_path: Path) -> None:
    """Publish the last-computed LEG demand forecast — kept on its own topic, never mixed with recent_actual data."""
    now = datetime.now(timezone.utc)
    prefix = config.topic_prefix
    qos = config.qos

    points = list_consumption_forecasts(db_path, scope="leg", start=now)
    series = [
        {
            "slot_start": p.slot_start.isoformat(),
            "forecast_kwh": p.forecast_kwh,
            "quality": p.quality,
            "sample_count": p.sample_count,
            "kind": "forecast",
        }
        for p in points
    ]

    near_term = [p for p in points if p.slot_start < now + timedelta(days=2)]
    ok_count = sum(1 for p in near_term if p.quality == "ok")
    overall_quality = "ok" if near_term and ok_count / len(near_term) >= 0.5 else "insufficient_data"

    method = points[0].method if points else None
    data_period_start = points[0].data_period_start.isoformat() if points and points[0].data_period_start else None
    data_period_end = points[0].data_period_end.isoformat() if points and points[0].data_period_end else None

    payload = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=config.energy_data_ttl_seconds)).isoformat(),
        "source": "shareomat",
        "quality": overall_quality,
        "data": {
            "scope": "leg", "method": method,
            "data_period_start": data_period_start, "data_period_end": data_period_end,
            "series": series,
        },
    }
    client.publish(f"{prefix}/energy_data/demand_forecast", json.dumps(payload), qos=qos, retain=True)
    logger.info("MQTT demand forecast published (%d slot(s), quality=%s)", len(series), overall_quality)


def publish_manual_demand_test(client: "_mqtt_type.Client", config: MqttConfig, value_kwh: float) -> None:
    """Publish a single manually-entered LEG demand value onto the real demand_forecast topic.

    Lets a developer exercise Emsomat's actual ingestion path (same topic,
    same envelope/series shape) from the Home Assistant GUI, without
    waiting for real accumulated meter data. Overwrites the retained
    demand_forecast payload until the next real settlement cycle republishes
    the computed forecast — intentional for a test aid, not a bug.
    """
    now = datetime.now(timezone.utc)
    slot_start = now - timedelta(
        minutes=now.minute % SLOT_MINUTES, seconds=now.second, microseconds=now.microsecond,
    )
    prefix = config.topic_prefix
    payload = {
        "schema_version": 1,
        "created_at": now.isoformat(),
        "valid_until": (now + timedelta(seconds=config.energy_data_ttl_seconds)).isoformat(),
        "source": "shareomat",
        "quality": "ok",
        "data": {
            "scope": "leg", "method": "manual_test_override",
            "data_period_start": None, "data_period_end": None,
            "series": [{
                "slot_start": slot_start.isoformat(), "forecast_kwh": value_kwh,
                "quality": "ok", "sample_count": 0, "kind": "forecast",
            }],
        },
    }
    client.publish(f"{prefix}/energy_data/demand_forecast", json.dumps(payload), qos=config.qos, retain=True)
    logger.info("MQTT manual demand test value published: %.3f kWh", value_kwh)


def setup_command_subscription(
    client: "_mqtt_type.Client",
    on_run: Callable[[], None],
    config: MqttConfig,
) -> None:
    """Register the run_once subscription on the client (non-blocking)."""
    cmd_topic = f"{config.topic_prefix}/cmd/run_once"
    auto_scan_topic = f"{config.topic_prefix}/auto_scan/set"
    demand_test_topic = f"{config.topic_prefix}/energy_data/demand_forecast/test_set"

    def on_message(client, userdata, message) -> None:
        """Dispatch an incoming command topic to the settlement trigger, auto-scan switch, or demand test value."""
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
        elif topic == demand_test_topic:
            payload = message.payload.decode("utf-8", errors="ignore").strip()
            try:
                value_kwh = float(payload)
            except ValueError:
                logger.warning("demand_forecast/test_set received non-numeric payload: %r", payload)
                return
            publish_manual_demand_test(client, config, value_kwh)

    client.on_message = on_message

    # Register all topics in userdata so on_connect re-subscribes after reconnect
    userdata = client.user_data_get() or {}
    subs = userdata.get("subscriptions", [])
    for topic in (cmd_topic, auto_scan_topic, demand_test_topic):
        if (topic, config.qos) not in subs:
            subs.append((topic, config.qos))
    userdata["subscriptions"] = subs
    client.user_data_set(userdata)

    client.subscribe(cmd_topic, qos=config.qos)
    client.subscribe(auto_scan_topic, qos=config.qos)
    client.subscribe(demand_test_topic, qos=config.qos)
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


def setup_mqtt(mqtt_config: MqttConfig) -> object | None:
    """Connect to the MQTT broker when enabled in config, or return None."""
    if not mqtt_config.enabled:
        return None
    client = create_client(mqtt_config)
    if client is None:
        logger.warning("MQTT requested but client could not connect — running without MQTT")
    return client


def should_run_daemon(
    mqtt_config: MqttConfig,
    mqtt_client: object | None,
    *,
    cron_schedule: str,
    auto_scan_enabled: bool,
) -> bool:
    """Return True when at least one daemon service is configured.

    Takes cron_schedule/auto_scan_enabled explicitly (rather than a full
    LegConfig) because they are only known once the database-backed
    OperationSettings can be read — which does not require a complete
    setup (community/participants/meters/tariff), unlike a full LegConfig.
    """
    return (
        (mqtt_client is not None and mqtt_config.command_topic_enabled)
        or bool(cron_schedule)
        or auto_scan_enabled
    )


def run_mqtt_daemon(
    mqtt_config: MqttConfig,
    mqtt_client: object,
    on_run: Callable[[], None],
) -> None:
    """Enter daemon mode: block and wait for run commands from the MQTT broker."""
    logger.info(
        "Daemon mode — publish any payload to %s/cmd/run_once to trigger a run",
        mqtt_config.topic_prefix,
    )
    start_command_listener(mqtt_client, on_run=on_run, config=mqtt_config)


def shutdown_mqtt(mqtt_client: object | None) -> None:
    """Gracefully disconnect from the MQTT broker."""
    if mqtt_client is None:
        return
    disconnect_client(mqtt_client)
