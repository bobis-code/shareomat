# -*- coding: utf-8 -*-
"""
File: shareomat/ha/live_sensors.py

Purpose:
    Home Assistant sensors of the live layer via MQTT Discovery (ADR-0007, I2).
    The site's own Emsomat values are shown as soon as they are valid; the
    LEG surplus only with a complete balance.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    An unknown value is published as "None", which Home Assistant shows as
    "unknown": never as 0. The sensors carry no data of other participants,
    only the LEG status and counts. They are read-only, no command topics.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from shareomat.config import MqttConfig
from shareomat.leg_const import MQTT_STATUS_OFFLINE, MQTT_STATUS_OK
from shareomat.live.model import (
    COMM_NEVER,
    COMM_OFFLINE,
    COMM_ONLINE,
    DISPLAY_NO_SURPLUS,
    DISPLAY_SURPLUS,
    QUALITY_CONFLICT,
    QUALITY_INVALID,
    QUALITY_MISSING,
    QUALITY_OK,
    QUALITY_STALE,
    STATUS_COMPLETE,
    STATUS_NO_DATA,
    STATUS_UNCERTAIN,
    LegStatus,
)

if TYPE_CHECKING:
    import paho.mqtt.client as _mqtt_type

logger = logging.getLogger(__name__)

UNKNOWN = "None"
_REFRESH_S = 60.0
_DEVICE = {
    "identifiers": ["shareomat_live"],
    "name": "Shareomat Live-Energie",
    "manufacturer": "Shareomat",
    "model": "LEG Live-Energie",
}
_QUALITY_OPTIONS = [QUALITY_OK, QUALITY_MISSING, QUALITY_INVALID, QUALITY_STALE, QUALITY_CONFLICT]
_COMM_OPTIONS = [COMM_ONLINE, COMM_OFFLINE, COMM_NEVER]
LEG_STATUS_UNSICHER = "unsicher"
LEG_STATUS_KEINE_DATEN = "keine_daten"
_LEG_STATUS_OPTIONS = [DISPLAY_SURPLUS, DISPLAY_NO_SURPLUS, LEG_STATUS_UNSICHER, LEG_STATUS_KEINE_DATEN]


@dataclass(frozen=True)
class LiveSensor:
    object_id: str
    name: str
    topic: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None
    options: tuple[str, ...] | None = None
    attributes: bool = False


LIVE_SENSORS: tuple[LiveSensor, ...] = (
    LiveSensor("shareomat_live_netzbezug", "Netzbezug", "own/import_w", "W", "power", "measurement", "mdi:transmission-tower-import"),
    LiveSensor("shareomat_live_netzeinspeisung", "Netzeinspeisung", "own/export_w", "W", "power", "measurement", "mdi:transmission-tower-export"),
    LiveSensor("shareomat_live_messwert_qualitaet", "Messwertqualität", "own/quality", device_class="enum",
               icon="mdi:check-decagram", options=tuple(_QUALITY_OPTIONS)),
    LiveSensor("shareomat_live_verbindung", "Verbindung", "own/communication", device_class="enum",
               icon="mdi:lan-connect", options=tuple(_COMM_OPTIONS)),
    LiveSensor("shareomat_live_messzeitpunkt", "Messzeitpunkt", "own/measured_at", device_class="timestamp", icon="mdi:clock-outline"),
    LiveSensor("shareomat_live_zeitquelle", "Herkunft Zeitstempel", "own/time_origin", icon="mdi:clock-question"),
    LiveSensor("shareomat_leg_live_status", "LEG-Live-Status", "leg/status", device_class="enum",
               icon="mdi:home-lightning-bolt", options=tuple(_LEG_STATUS_OPTIONS), attributes=True),
    LiveSensor("shareomat_leg_vollstaendigkeit", "LEG-Vollständigkeit", "leg/coverage", "%", None, "measurement", "mdi:percent"),
    LiveSensor("shareomat_leg_ueberschuss", "LEG-Überschuss", "leg/surplus_kw", "kW", "power", "measurement", "mdi:solar-power"),
)


def publish_live_discovery(client: "_mqtt_type.Client", config: MqttConfig) -> None:
    """Retained Discovery for all live sensors, so HA knows them after a restart."""
    prefix = f"{config.topic_prefix}/live"
    availability = [{
        "topic": f"{config.topic_prefix}/status",
        # Any engine status except the broker's "offline" Will means the live layer runs.
        "value_template": f"{{{{ '{MQTT_STATUS_OFFLINE}' if value == '{MQTT_STATUS_OFFLINE}' else '{MQTT_STATUS_OK}' }}}}",
        "payload_available": MQTT_STATUS_OK,
        "payload_not_available": MQTT_STATUS_OFFLINE,
    }]
    for sensor in LIVE_SENSORS:
        payload: dict = {
            "name": sensor.name,
            "unique_id": sensor.object_id,
            "default_entity_id": f"sensor.{sensor.object_id}",
            "state_topic": f"{prefix}/{sensor.topic}",
            "availability": availability,
            "device": _DEVICE,
        }
        if sensor.unit:
            payload["unit_of_measurement"] = sensor.unit
        if sensor.device_class:
            payload["device_class"] = sensor.device_class
        if sensor.state_class:
            payload["state_class"] = sensor.state_class
        if sensor.icon:
            payload["icon"] = sensor.icon
        if sensor.options:
            payload["options"] = list(sensor.options)
        if sensor.attributes:
            payload["json_attributes_topic"] = f"{prefix}/{sensor.topic}/attributes"
        client.publish(f"{config.discovery_prefix}/sensor/{sensor.object_id}/config", json.dumps(payload), qos=config.qos, retain=True)
    logger.info("HA Discovery published: %d live sensors", len(LIVE_SENSORS))


def _number(value: float | None, digits: int) -> str:
    return UNKNOWN if value is None else str(round(value, digits))


def leg_status_state(status: LegStatus) -> str:
    if status.status == STATUS_COMPLETE:
        return status.display_state
    return LEG_STATUS_UNSICHER if status.status == STATUS_UNCERTAIN else LEG_STATUS_KEINE_DATEN


def surplus_state(status: LegStatus) -> str:
    """A number only with a complete balance. "No surplus" of a complete balance is a true 0."""
    if status.status != STATUS_COMPLETE:
        return UNKNOWN
    if status.display_state == DISPLAY_SURPLUS and status.display_kw is not None:
        return str(status.display_kw)
    return "0"


def build_states(service, status: LegStatus) -> dict[str, str]:
    """topic suffix -> payload for all live sensors."""
    own = service.own_reading()
    usable = own.configured and own.quality == QUALITY_OK
    attributes = {
        "vollstaendig": status.status == STATUS_COMPLETE,
        "messpunkte_gueltig": status.ok_count,
        "messpunkte_erwartet": status.expected_count,
        "berechnet": status.updated_at.isoformat(),
    }
    return {
        "own/import_w": _number(own.import_w if usable else None, 1),
        "own/export_w": _number(own.export_w if usable else None, 1),
        "own/quality": own.quality,
        "own/communication": own.communication,
        "own/measured_at": own.measured_at.isoformat() if usable and own.measured_at else UNKNOWN,
        "own/time_origin": (own.time_origin or UNKNOWN) if usable else UNKNOWN,
        "leg/status": leg_status_state(status),
        "leg/status/attributes": json.dumps(attributes, sort_keys=True),
        "leg/coverage": str(round(status.coverage * 100)),
        "leg/surplus_kw": surplus_state(status),
    }


class LiveHaPublisher:
    """Listener of LiveStatusService: publishes changes at once, everything once a minute."""

    def __init__(self, client: "_mqtt_type.Client", config: MqttConfig) -> None:
        self._client = client
        self._config = config
        self._last: dict[str, str] = {}
        self._last_full = 0.0

    def __call__(self, service, status: LegStatus) -> None:
        states = build_states(service, status)
        now = time.monotonic()
        full = now - self._last_full >= _REFRESH_S
        if full:
            self._last_full = now
        prefix = f"{self._config.topic_prefix}/live"
        for topic, payload in states.items():
            if full or self._last.get(topic) != payload:
                self._client.publish(f"{prefix}/{topic}", payload, qos=self._config.qos, retain=True)
        self._last = states


__all__ = ["LIVE_SENSORS", "LiveHaPublisher", "publish_live_discovery", "build_states", "STATUS_NO_DATA"]
