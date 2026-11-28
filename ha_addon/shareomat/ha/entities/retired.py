# -*- coding: utf-8 -*-
"""
File: shareomat/ha/entities/retired.py

Purpose:
    Home Assistant entities that only the CSV settlement run filled and that
    were removed with it in 0.6.34 (ADR-0012, P2d). mqtt_discovery.
    retire_entities() sends an empty retained message to their Discovery
    topics, so Home Assistant removes them instead of keeping them as
    "unavailable", and clears their retained state topics on the broker.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Unique IDs, Discovery topics and state topics are exactly the ones the
    removed code published (see git history of mqtt_discovery.py and
    entities/{sensors,buttons,switches}.py before 0.6.34). The entity
    "Shareomat Engine Status" is not retired: it stays and is published at
    startup.
"""

from __future__ import annotations

# (HA domain, unique_id suffix after "shareomat_engine_") under the engine device.
RETIRED_ENGINE_ENTITIES: tuple[tuple[str, str], ...] = (
    ("sensor", "last_run"),
    ("sensor", "inbox_count"),
    ("sensor", "report_count"),
    ("sensor", "last_error"),
    ("button", "run_now"),
    ("switch", "auto_scan"),
)

# Retained state topics of the engine device, below the topic prefix.
RETIRED_ENGINE_STATE_TOPICS: tuple[str, ...] = (
    "last_run", "inbox_count", "report_count", "last_error", "auto_scan/state",
)

# Per-participant billing sensors: unique_id "shareomat_{participant}_{field}",
# state topic "{prefix}/billing/{participant}/{field}".
RETIRED_BILLING_FIELDS: tuple[str, ...] = (
    "total_cost_chf", "local_cost_chf", "grid_cost_chf", "local_share_kwh", "grid_import_kwh", "total_import_kwh",
)
