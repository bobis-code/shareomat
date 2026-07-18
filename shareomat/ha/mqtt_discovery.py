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
           Registers the engine device (system sensors, button, optional switch).
        2. publish_billing_discovery() — after each successful settlement cycle.
           Registers billing sensors per participant under the community device.

        publish_ha_discovery() is a convenience wrapper that calls both.

    All Discovery payloads are always retained so HA re-registers entities
    on restart without requiring a new settlement run.

    System state topics are published by mqtt_runtime.publish_system_state(),
    not here. Discovery only tells HA where to look.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from shareomat.core.leg_config import MqttConfig
from shareomat.leg_const import MQTT_STATUS_OFFLINE, MQTT_STATUS_OK
from shareomat.models.invoice import BillingRecord
from shareomat.ha.mqtt_entities import (
    _HA_BILLING_SENSORS,
    _HA_BUTTONS,
    _HA_SWITCHES,
    _HA_SYSTEM_ENTITIES,
    _topic_safe,
)

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
    """Publish HA Discovery for the engine device: system sensors, button, optional switch."""
    prefix = config.topic_prefix
    discovery_prefix = config.discovery_prefix
    qos = config.qos
    device = _engine_device()

    # System sensors (status, last_run, inbox_count, report_count, last_error)
    for uid_suffix, name, topic_suffix, device_class, state_class, icon in _HA_SYSTEM_ENTITIES:
        unique_id = f"shareomat_engine_{uid_suffix}"
        payload: dict = {
            "name": name,
            "unique_id": unique_id,
            "state_topic": f"{prefix}/{topic_suffix}",
            "device": device,
        }
        if device_class:
            payload["device_class"] = device_class
        if state_class:
            payload["state_class"] = state_class
        if icon:
            payload["icon"] = icon
        client.publish(
            f"{discovery_prefix}/sensor/{unique_id}/config",
            json.dumps(payload), qos=qos, retain=True,
        )

    # Buttons (Run Now)
    for uid_suffix, name, cmd_suffix, payload_press, icon in _HA_BUTTONS:
        unique_id = f"shareomat_engine_{uid_suffix}"
        payload = {
            "name": name,
            "unique_id": unique_id,
            "command_topic": f"{prefix}/{cmd_suffix}",
            "payload_press": payload_press,
            "device": device,
        }
        if icon:
            payload["icon"] = icon
        client.publish(
            f"{discovery_prefix}/button/{unique_id}/config",
            json.dumps(payload), qos=qos, retain=True,
        )

    # Switch: only published when auto-scan is configured
    if auto_scan_enabled:
        for uid_suffix, name, state_suffix, cmd_suffix, pay_on, pay_off, icon in _HA_SWITCHES:
            unique_id = f"shareomat_engine_{uid_suffix}"
            payload = {
                "name": name,
                "unique_id": unique_id,
                "state_topic": f"{prefix}/{state_suffix}",
                "command_topic": f"{prefix}/{cmd_suffix}",
                "payload_on": pay_on,
                "payload_off": pay_off,
                "device": device,
            }
            if icon:
                payload["icon"] = icon
            client.publish(
                f"{discovery_prefix}/switch/{unique_id}/config",
                json.dumps(payload), qos=qos, retain=True,
            )
        logger.info("HA Discovery: auto-scan switch published")

    logger.info("HA Discovery published: engine device (sensors, button%s)",
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

        for field, friendly, unit, device_class, state_class in _HA_BILLING_SENSORS:
            unique_id = f"shareomat_{pid_safe}_{field}"
            payload: dict = {
                "name": f"{rec.label} {friendly}",
                "unique_id": unique_id,
                "state_topic": f"{base_state}/{field}",
                "unit_of_measurement": unit,
                "state_class": state_class,
                "availability": availability,
                "device": device,
            }
            if device_class:
                payload["device_class"] = device_class

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
