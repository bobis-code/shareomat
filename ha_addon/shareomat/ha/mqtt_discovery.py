# -*- coding: utf-8 -*-
"""
File: shareomat/ha/mqtt_discovery.py

Purpose:
    Home Assistant MQTT Discovery for the Shareomat Engine device, and the
    removal of the entities the CSV settlement run used to fill.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Home Assistant integration uses pure MQTT Discovery — no HA Python
    library or API dependency of any kind.

    Called once at startup (main.py), after the MQTT connection is up:
        1. publish_engine_discovery() — the engine device with its status
           sensor (and number inputs, none at present). Until 0.6.33 this
           only happened after a CSV run (B26).
        2. retire_entities() — empty retained Discovery messages for the
           entities removed in 0.6.34 (entities/retired.py), so Home
           Assistant deletes them; their retained values are cleared too.

    All Discovery payloads are retained, so HA re-registers the entities on
    its own restart. The live sensors have their own Discovery
    (live_sensors.py).

    Entity definitions live in shareomat/ha/entities/ (one file per HA
    domain) — this module only turns them into Discovery JSON payloads.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Iterable

from shareomat.config import MqttConfig
from shareomat.ha.entities import _topic_safe
from shareomat.ha.entities.numbers import NUMBERS
from shareomat.ha.entities.retired import (
    RETIRED_BILLING_FIELDS,
    RETIRED_ENGINE_ENTITIES,
    RETIRED_ENGINE_STATE_TOPICS,
)
from shareomat.ha.entities.sensors import SYSTEM_SENSORS

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


# ── Public interface ─────────────────────────────────────────────────────────


def publish_engine_discovery(client: "_mqtt_type.Client", config: MqttConfig) -> None:
    """Publish HA Discovery for the engine device: system sensors and number inputs."""
    prefix = config.topic_prefix
    discovery_prefix = config.discovery_prefix
    qos = config.qos
    device = _engine_device()

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

    logger.info("HA Discovery published: engine device (%d sensor(s))", len(SYSTEM_SENSORS))


def retire_entities(client: "_mqtt_type.Client", config: MqttConfig, participant_ids: Iterable[str]) -> None:
    """Remove the entities of the CSV run from HA and clear their retained values on the broker.

    An empty retained message deletes the retained Discovery config, and HA
    removes the entity. Safe to repeat on every start. `participant_ids`
    are all participants in the database; a participant deleted before
    0.6.34 keeps its old billing sensors (not known any more).
    """
    prefix = config.topic_prefix
    discovery_prefix = config.discovery_prefix
    qos = config.qos

    topics = [f"{discovery_prefix}/{domain}/shareomat_engine_{suffix}/config"
              for domain, suffix in RETIRED_ENGINE_ENTITIES]
    topics += [f"{prefix}/{suffix}" for suffix in RETIRED_ENGINE_STATE_TOPICS]
    participants = sorted({_topic_safe(pid) for pid in participant_ids})
    for pid_safe in participants:
        for field in RETIRED_BILLING_FIELDS:
            topics.append(f"{discovery_prefix}/sensor/shareomat_{pid_safe}_{field}/config")
            topics.append(f"{prefix}/billing/{pid_safe}/{field}")
    for topic in topics:
        client.publish(topic, "", qos=qos, retain=True)
    logger.info("HA Discovery: entities of the former CSV run removed (engine, %d participant(s))", len(participants))
