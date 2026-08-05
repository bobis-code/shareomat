# -*- coding: utf-8 -*-
"""
File: shareomat/ha/entities/__init__.py

Purpose:
    HA MQTT Discovery entity definitions, one file per HA domain (mirrors
    the structure of external MQTT consumers like Emsomat's entity/
    package — each domain is its own file with a small dataclass plus a
    list of instances; adding a new entity is one line in the matching
    list). mqtt_discovery.py consumes these definitions to build the JSON
    Discovery payloads; mqtt_runtime.py's command handlers reference the
    command-topic suffixes defined here.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations


def _topic_safe(s: str) -> str:
    """Sanitize a string for safe use in MQTT topic paths and Home Assistant entity IDs."""
    return s.lower().replace(" ", "_").replace("/", "_").replace(":", "_")
