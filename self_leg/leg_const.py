# -*- coding: utf-8 -*-
"""
File: self_leg/leg_const.py

Purpose:
    Domain-wide constants for the SELF LEG settlement engine.
    Single source of truth for directions, roles, file extensions,
    slot geometry, status values, and configuration defaults.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure constants — no imports, no business logic.
    If a string literal appears in more than one module, it belongs here.
"""

from __future__ import annotations

# ── Slot geometry ─────────────────────────────────────────────────────────────

SLOT_MINUTES: int = 15
SLOTS_PER_HOUR: int = 60 // SLOT_MINUTES
SLOTS_PER_DAY: int = 24 * SLOTS_PER_HOUR

# ── Energy flow directions ────────────────────────────────────────────────────

DIRECTION_EXPORT: str = "export"
DIRECTION_IMPORT: str = "import"

# ── Meter roles ───────────────────────────────────────────────────────────────

METER_ROLE_PRODUCER: str = "producer"
METER_ROLE_CONSUMER: str = "consumer"
METER_ROLE_PRODUCER_CONSUMER: str = "producer_consumer"
METER_ROLE_GRID: str = "grid"

# ── Participant types ─────────────────────────────────────────────────────────

PARTICIPANT_TYPE_PRODUCER: str = "producer"
PARTICIPANT_TYPE_CONSUMER: str = "consumer"
PARTICIPANT_TYPE_PRODUCER_CONSUMER: str = "producer_consumer"

# ── Unknown meter handling ────────────────────────────────────────────────────

UNKNOWN_METER_POLICY_FAIL: str = "fail"
UNKNOWN_METER_POLICY_SKIP: str = "skip"

# ── MQTT status values ────────────────────────────────────────────────────────

MQTT_STATUS_STARTING: str = "starting"
MQTT_STATUS_OK: str = "ok"
MQTT_STATUS_ERROR: str = "error"
MQTT_STATUS_OFFLINE: str = "offline"

# ── File extensions ───────────────────────────────────────────────────────────

FILE_EXT_CSV: str = ".csv"
FILE_EXT_XML: str = ".xml"
FILE_EXT_SDAT: str = ".sdat"
FILE_EXT_XLSX: str = ".xlsx"

# ── Reading quality flags ─────────────────────────────────────────────────────

QUALITY_VALID: str = "valid"
QUALITY_ESTIMATED: str = "estimated"
QUALITY_INVALID: str = "invalid"

# ── Configuration defaults ────────────────────────────────────────────────────

DEFAULT_SLOT_MINUTES: int = SLOT_MINUTES
DEFAULT_MQTT_PORT: int = 1883
DEFAULT_TOPIC_PREFIX: str = "self_leg"
DEFAULT_IMAP_PORT: int = 993
