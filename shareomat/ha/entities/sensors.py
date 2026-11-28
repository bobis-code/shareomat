# -*- coding: utf-8 -*-
"""
File: shareomat/ha/entities/sensors.py

Purpose:
    Sensor entity definitions under the Shareomat Engine device. Add a new
    sensor by adding one line to SYSTEM_SENSORS. The sensors of the former
    CSV run (last run, inbox, reports, last error, billing per participant)
    were removed in 0.6.34 and are listed in retired.py.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SystemSensorDef:
    """One system-health sensor published under the Shareomat Engine device."""

    uid_suffix: str
    name: str
    state_topic_suffix: str
    device_class: str | None = None
    state_class: str | None = None
    icon: str | None = None


SYSTEM_SENSORS: list[SystemSensorDef] = [
    SystemSensorDef("status", "Shareomat Engine Status", "status", icon="mdi:state-machine"),
]
