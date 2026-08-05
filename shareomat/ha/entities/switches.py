# -*- coding: utf-8 -*-
"""
File: shareomat/ha/entities/switches.py

Purpose:
    Switch entity definitions, published under the Shareomat Engine device.
    Add a new switch by adding one line to SWITCHES.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SwitchDef:
    """One toggle switch, with separate state and command topics."""

    uid_suffix: str
    name: str
    state_topic_suffix: str
    command_topic_suffix: str
    payload_on: str
    payload_off: str
    icon: str | None = None


# Only published when auto-scan is configured (see mqtt_discovery.publish_engine_discovery).
SWITCHES: list[SwitchDef] = [
    SwitchDef("auto_scan", "Shareomat Engine Auto Scan", "auto_scan/state", "auto_scan/set",
              "ON", "OFF", icon="mdi:magnify-scan"),
]
