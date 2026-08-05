# -*- coding: utf-8 -*-
"""
File: shareomat/ha/entities/sensors.py

Purpose:
    Sensor entity definitions: system health (under the Shareomat Engine
    device) and per-participant billing figures (under the community
    device). Add a new sensor by adding one line to the matching list.

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


@dataclass(frozen=True)
class BillingSensorDef:
    """One per-participant billing sensor field, published under the community device."""

    field: str
    friendly: str
    unit: str
    device_class: str | None = None
    state_class: str = "total"


SYSTEM_SENSORS: list[SystemSensorDef] = [
    SystemSensorDef("status", "Shareomat Engine Status", "status", icon="mdi:state-machine"),
    SystemSensorDef("last_run", "Shareomat Engine Last Run", "last_run",
                     device_class="timestamp", icon="mdi:clock-check"),
    SystemSensorDef("inbox_count", "Shareomat Engine Inbox Files", "inbox_count",
                     state_class="measurement", icon="mdi:inbox"),
    SystemSensorDef("report_count", "Shareomat Engine Report Count", "report_count",
                     state_class="measurement", icon="mdi:file-chart"),
    SystemSensorDef("last_error", "Shareomat Engine Last Error", "last_error", icon="mdi:alert-circle"),
]

BILLING_SENSORS: list[BillingSensorDef] = [
    BillingSensorDef("total_cost_chf", "Total Cost", "CHF"),
    BillingSensorDef("local_cost_chf", "Local Energy Cost", "CHF"),
    BillingSensorDef("grid_cost_chf", "Grid Energy Cost", "CHF"),
    BillingSensorDef("local_share_kwh", "Local Share", "kWh", device_class="energy"),
    BillingSensorDef("grid_import_kwh", "Grid Import", "kWh", device_class="energy"),
    BillingSensorDef("total_import_kwh", "Total Import", "kWh", device_class="energy"),
]
