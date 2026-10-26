# -*- coding: utf-8 -*-
"""
File: shareomat/leg_const.py

Purpose:
    Domain-wide constants for the Shareomat settlement engine.
    Single source of truth for directions, roles, file extensions,
    slot geometry, status values, and configuration defaults.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

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
# Storage meters are both LEG-Bezüger and LEG-Produzent (see LEG-Mustervertrag
# Beitrittserklärung), and per Art. 19h Abs. 4 StromVV must not, in sum, feed more
# electricity into the community per settlement period than they draw from it.
METER_ROLE_STORAGE: str = "storage"

# A participant has no type of its own — what it does in the LEG (beziehen,
# liefern, Speicher) follows from the roles of its meters, see
# shareomat.core.participant_roles.

# ── Unknown meter handling ────────────────────────────────────────────────────

UNKNOWN_METER_POLICY_FAIL: str = "fail"
UNKNOWN_METER_POLICY_SKIP: str = "skip"

# ── Meter data source ─────────────────────────────────────────────────────────
# Per-community switch (see shareomat.models.settings.OperationSettings):
# how the community *receives* meter data. Independent of file_type routing
# in leg_import.py/leg_runner.py, which picks a parser by extension — this
# picks which parser handles the "sdat" file_type (see shareomat.core.leg_runner
# .parse_file and docs/sdat_leg_import.md).

METER_DATA_SOURCE_EMAIL_CSV: str = "email_csv"
METER_DATA_SOURCE_SDAT_LEG: str = "sdat_leg"

# ── Billing run lifecycle ─────────────────────────────────────────────────────
# A released billing run is an immutable snapshot; only draft -> released and
# draft|released -> cancelled transitions are allowed (see shareomat.database.billing).

BILLING_STATUS_DRAFT: str = "draft"
BILLING_STATUS_RELEASED: str = "released"
BILLING_STATUS_CANCELLED: str = "cancelled"

# ── Contract version lifecycle ────────────────────────────────────────────────
# Only two stored statuses. Whether a "published" version is currently in
# force, merely announced for the future, or historical is never a third
# stored status — it is derived from valid_from/valid_until at read time,
# exactly like tariffs. See shareomat.database.contract_versions.

CONTRACT_STATUS_DRAFT: str = "draft"
CONTRACT_STATUS_PUBLISHED: str = "published"
# A published version withdrawn before taking effect after participants had
# already signed/been notified/been reported: kept read-only with all its
# assignments as evidence instead of being turned back into a draft.
CONTRACT_STATUS_WITHDRAWN: str = "withdrawn"

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
DEFAULT_TOPIC_PREFIX: str = "shareomat"
DEFAULT_IMAP_PORT: int = 993
