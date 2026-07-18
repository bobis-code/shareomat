# -*- coding: utf-8 -*-
"""
File: shareomat/ha/mqtt_entities.py

Purpose:
    Central registry of all Home Assistant entities that Shareomat exposes
    via MQTT Discovery. Adding an entity here is enough to make it appear
    in Home Assistant — no other code changes required.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Two HA devices are registered:

    Shareomat Engine  (identifier: shareomat_engine)
        System sensors: status, last_run, inbox_count, report_count, last_error
        Button:         Run Now  → publishes "run" to cmd/run_once
        Switch:         Auto Scan (placeholder — engine does not yet publish state)

    <community_name>  (identifier: shareomat_<community_id>)
        Billing sensors: one set per participant (cost + energy totals)

    Definitions are consumed by mqtt_discovery.py to build the JSON payloads
    and by mqtt_runtime.py for topic routing.
"""

from __future__ import annotations

# ── Billing sensors (community device, one set per participant) ───────────────
# (field_name, friendly_suffix, unit, device_class | None, state_class)
_HA_BILLING_SENSORS: list[tuple[str, str, str, str | None, str]] = [
    ("total_cost_chf",   "Total Cost",        "CHF", None,     "total"),
    ("local_cost_chf",   "Local Energy Cost", "CHF", None,     "total"),
    ("grid_cost_chf",    "Grid Energy Cost",  "CHF", None,     "total"),
    ("local_share_kwh",  "Local Share",       "kWh", "energy", "total"),
    ("grid_import_kwh",  "Grid Import",       "kWh", "energy", "total"),
    ("total_import_kwh", "Total Import",      "kWh", "energy", "total"),
]

# ── System sensors (engine device) ───────────────────────────────────────────
# (unique_id_suffix, friendly_name, state_topic_suffix,
#  device_class | None, state_class | None, icon | None)
_HA_SYSTEM_ENTITIES: list[tuple[str, str, str, str | None, str | None, str | None]] = [
    ("status",       "Shareomat Engine Status",       "status",       None,        None,          "mdi:state-machine"),
    ("last_run",     "Shareomat Engine Last Run",      "last_run",     "timestamp", None,          "mdi:clock-check"),
    ("inbox_count",  "Shareomat Engine Inbox Files",   "inbox_count",  None,        "measurement", "mdi:inbox"),
    ("report_count", "Shareomat Engine Report Count",  "report_count", None,        "measurement", "mdi:file-chart"),
    ("last_error",   "Shareomat Engine Last Error",    "last_error",   None,        None,          "mdi:alert-circle"),
]

# ── Buttons (engine device) ───────────────────────────────────────────────────
# (unique_id_suffix, friendly_name, command_topic_suffix, payload_press, icon | None)
_HA_BUTTONS: list[tuple[str, str, str, str, str | None]] = [
    ("run_now", "Shareomat Engine Run Now", "cmd/run_once", "run", "mdi:play-circle"),
]

# ── Switches (engine device) ──────────────────────────────────────────────────
# (unique_id_suffix, friendly_name, state_topic_suffix, command_topic_suffix,
#  payload_on, payload_off, icon | None)
# Not yet published — will be enabled here when auto-scan is implemented.
_HA_SWITCHES: list[tuple[str, str, str, str, str, str, str | None]] = [
    ("auto_scan", "Shareomat Engine Auto Scan", "auto_scan/state", "auto_scan/set", "ON", "OFF", "mdi:magnify-scan"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _topic_safe(s: str) -> str:
    """Sanitize a string for safe use in MQTT topic paths and Home Assistant entity IDs."""
    return s.lower().replace(" ", "_").replace("/", "_").replace(":", "_")
