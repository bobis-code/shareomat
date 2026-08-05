# -*- coding: utf-8 -*-
"""
File: shareomat/ha/mqtt_discovery.py

Purpose:
    Home Assistant MQTT Discovery payload publisher.
    Publishes two categories of discovery payloads so that all entities
    appear automatically in Home Assistant without manual configuration.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Home Assistant integration uses pure MQTT Discovery — no HA Python
    library or API dependency of any kind.

    Call order:
        1. publish_engine_discovery() — at startup, before the first billing run.
           Registers the engine device (system sensors, button, number, optional switch).
        2. publish_billing_discovery() — after each successful settlement cycle.
           Registers billing sensors per participant under the community device.

        publish_ha_discovery() is a convenience wrapper that calls both.

    All Discovery payloads are always retained so HA re-registers entities
    on restart without requiring a new settlement run.

    System state topics are published by mqtt_runtime.publish_system_state(),
    not here. Discovery only tells HA where to look.

    Entity definitions themselves live in shareomat/ha/entities/ (one file
    per HA domain) — this module only turns those definitions into
    Discovery JSON payloads.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from shareomat.config import MqttConfig
from shareomat.leg_const import MQTT_STATUS_OFFLINE, MQTT_STATUS_OK
from shareomat.models.billing import BillingRecord
from shareomat.ha.entities import _topic_safe
from shareomat.ha.entities.buttons import BUTTONS
from shareomat.ha.entities.numbers import NUMBERS
from shareomat.ha.entities.sensors import BILLING_SENSORS, SYSTEM_SENSORS
from shareomat.ha.entities.switches import SWITCHES

if TYPE_CHECKING:
    import paho.mqtt.client as _mqtt_type

logger = logging.getLogger(__name__)

_ENGINE_DEVICE_ID = "shareomat_engine"


def _engine_device() -> dict:
    return {
        "identifiers": [_ENGINE_DEVICE_ID],
        "name": "Shareomat Engine",
        "manufacturer": "Shareomat",
        "model": "LEG/ZEV Settlement Engine",
    }


def publish_engine_discovery(
    client: "_mqtt_type.Client",
    config: MqttConfig,
    *,
    auto_scan_enabled: bool = False,
) -> None:
    """Publish HA Discovery for the engine device: system sensors, button, number, optional switch."""
    prefix = config.topic_prefix
    discovery_prefix = config.discovery_prefix
    qos = config.qos
    device = _engine_device()

    # System sensors (status, last_run, inbox_count, report_count, last_error)
    for sensor in SYSTEM_SENSORS:
        unique_id = f"shareomat_engine_{sensor.uid_suffix}"
        payload: dict = {
            "name": sensor.name,
            "unique_id": unique_id,
            "state_topic": f"{prefix}/{sensor.state_topic_suffix}",
            "device": device,
        }
        if sensor.device_class:
            payload["device_class"] = sensor.device_class
        if sensor.state_class:
            payload["state_class"] = sensor.state_class
        if sensor.icon:
            payload["icon"] = sensor.icon
        client.publish(
            f"{discovery_prefix}/sensor/{unique_id}/config",
            json.dumps(payload), qos=qos, retain=True,
        )

    # Buttons (Run Now)
    for button in BUTTONS:
        unique_id = f"shareomat_engine_{button.uid_suffix}"
        payload = {
            "name": button.name,
            "unique_id": unique_id,
            "command_topic": f"{prefix}/{button.command_topic_suffix}",
            "payload_press": button.payload_press,
            "device": device,
        }
        if button.icon:
            payload["icon"] = button.icon
        client.publish(
            f"{discovery_prefix}/button/{unique_id}/config",
            json.dumps(payload), qos=qos, retain=True,
        )

    # Numbers (e.g. manual demand-forecast test value)
    for number in NUMBERS:
        unique_id = f"shareomat_engine_{number.uid_suffix}"
        payload = {
            "name": number.name,
            "unique_id": unique_id,
            "command_topic": f"{prefix}/{number.command_topic_suffix}",
            "unit_of_measurement": number.unit,
            "min": number.min_value,
            "max": number.max_value,
            "step": number.step,
            "mode": "box",
            "optimistic": True,
            "device": device,
        }
        if number.icon:
            payload["icon"] = number.icon
        client.publish(
            f"{discovery_prefix}/number/{unique_id}/config",
            json.dumps(payload), qos=qos, retain=True,
        )

    # Switch: only published when auto-scan is configured
    if auto_scan_enabled:
        for switch in SWITCHES:
            unique_id = f"shareomat_engine_{switch.uid_suffix}"
            payload = {
                "name": switch.name,
                "unique_id": unique_id,
                "state_topic": f"{prefix}/{switch.state_topic_suffix}",
                "command_topic": f"{prefix}/{switch.command_topic_suffix}",
                "payload_on": switch.payload_on,
                "payload_off": switch.payload_off,
                "device": device,
            }
            if switch.icon:
                payload["icon"] = switch.icon
            client.publish(
                f"{discovery_prefix}/switch/{unique_id}/config",
                json.dumps(payload), qos=qos, retain=True,
            )
        logger.info("HA Discovery: auto-scan switch published")

    logger.info("HA Discovery published: engine device (sensors, button, number%s)",
                ", switch" if auto_scan_enabled else "")


def publish_billing_discovery(
    client: "_mqtt_type.Client",
    records: list[BillingRecord],
    community_id: str,
    community_name: str,
    config: MqttConfig,
) -> None:
    """Publish HA Discovery for billing sensors — one set of sensors per participant."""
    prefix = config.topic_prefix
    discovery_prefix = config.discovery_prefix
    qos = config.qos
    device_id = f"shareomat_{_topic_safe(community_id)}"

    device = {
        "identifiers": [device_id],
        "name": community_name,
        "manufacturer": "Shareomat",
        "model": "LEG/ZEV Settlement Engine",
    }

    availability = [
        {
            "topic": f"{prefix}/status",
            "payload_available": MQTT_STATUS_OK,
            "payload_not_available": MQTT_STATUS_OFFLINE,
        }
    ]

    for rec in records:
        pid_safe = _topic_safe(rec.participant_id)
        base_state = f"{prefix}/billing/{pid_safe}"

        for sensor in BILLING_SENSORS:
            unique_id = f"shareomat_{pid_safe}_{sensor.field}"
            payload: dict = {
                "name": f"{rec.label} {sensor.friendly}",
                "unique_id": unique_id,
                "state_topic": f"{base_state}/{sensor.field}",
                "unit_of_measurement": sensor.unit,
                "state_class": sensor.state_class,
                "availability": availability,
                "device": device,
            }
            if sensor.device_class:
                payload["device_class"] = sensor.device_class

            client.publish(
                f"{discovery_prefix}/sensor/{unique_id}/config",
                json.dumps(payload), qos=qos, retain=True,
            )

        logger.debug("HA billing discovery published for %s (%s)", rec.participant_id, rec.label)

    logger.info("HA Discovery published: %d billing participant(s)", len(records))


def publish_ha_discovery(
    client: "_mqtt_type.Client",
    records: list[BillingRecord],
    community_id: str,
    community_name: str,
    config: MqttConfig,
    *,
    auto_scan_enabled: bool = False,
) -> None:
    """Publish all HA Discovery payloads: engine device + billing sensors per participant."""
    publish_engine_discovery(client, config, auto_scan_enabled=auto_scan_enabled)
    publish_billing_discovery(client, records, community_id, community_name, config)
