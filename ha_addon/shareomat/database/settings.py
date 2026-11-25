# -*- coding: utf-8 -*-
"""
File: shareomat/database/settings.py

Purpose:
    Read and write community-level operating settings (OperationSettings)
    in SQLite, stored as generic key/value rows.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The `settings` table is a generic key/value store so new switches can
    be added later without a schema migration; get_operation_settings()/
    save_operation_settings() give the typed, whole-object view the admin
    UI and config_builder actually use.

    The automatic Swisseldex sync (get_/save_swisseldex_schedule) has its
    own two keys: they stay absent until the operator saves them on
    "Externe Daten", and while absent the add-on options apply.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, fields
from pathlib import Path

from shareomat.config import SWISSELDEX_SYNC_INTERVALS_HOURS
from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, now_iso
from shareomat.models.settings import OperationSettings, SwisseldexSchedule

logger = logging.getLogger(__name__)

# bool/int/str fields on OperationSettings, in the order they are (de)serialized.
_BOOL_FIELDS = {"archive_processed", "auto_scan_enabled", "auto_create_billing",
                "auto_create_invoices", "auto_send_invoices", "peak_weekdays_only"}
_INT_FIELDS = {"scan_interval_seconds", "peak_start_hour", "peak_end_hour"}

# Automatic Swisseldex sync (SwisseldexSchedule): own keys, absent until saved on "Externe Daten".
SWISSELDEX_AUTO_SYNC_KEY = "swisseldex_auto_sync"
SWISSELDEX_INTERVAL_KEY = "swisseldex_sync_interval_hours"


# ── Private helpers ──────────────────────────────────────────────────────────


def _upsert(conn, community_row_id: int, key: str, value: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO settings (community_id, setting_key, setting_value, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(community_id, setting_key)
        DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
        """,
        (community_row_id, key, value, now),
    )


# ── Public functions ─────────────────────────────────────────────────────────


def get_setting(db_path: Path, key: str) -> str | None:
    """Return one raw setting value by key, or None if unset."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT setting_value FROM settings WHERE setting_key = ?", (key,)
        ).fetchone()
        return row["setting_value"] if row else None


def set_setting(db_path: Path, key: str, value: str) -> None:
    """Create or update one raw setting value by key."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            _upsert(conn, ensure_community_row_id(conn), key, value, now)


def get_operation_settings(db_path: Path) -> OperationSettings:
    """Return the community's operating settings, falling back to defaults for unset keys."""
    defaults = OperationSettings()
    with connect(db_path) as conn:
        rows = conn.execute("SELECT setting_key, setting_value FROM settings").fetchall()
    stored = {r["setting_key"]: r["setting_value"] for r in rows}

    values = {}
    for f in fields(OperationSettings):
        raw = stored.get(f.name)
        if raw is None:
            values[f.name] = getattr(defaults, f.name)
        elif f.name in _BOOL_FIELDS:
            values[f.name] = raw == "1"
        elif f.name in _INT_FIELDS:
            values[f.name] = int(raw)
        else:
            values[f.name] = raw
    return OperationSettings(**values)


def save_operation_settings(db_path: Path, settings: OperationSettings) -> OperationSettings:
    """Persist the community's operating settings as individual key/value rows."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            for key, value in asdict(settings).items():
                stored_value = "1" if value is True else "0" if value is False else str(value)
                _upsert(conn, community_row_id, key, stored_value, now)
    return get_operation_settings(db_path)


def swisseldex_interval_from_text(text: str | None) -> int | None:
    """One of the offered intervals from its exact text ("1", "3", "6", "12", "24"), else None.

    Compared as text on purpose: str.isdigit() also accepts characters such
    as "²" that int() then rejects.
    """
    by_text = {str(hours): hours for hours in SWISSELDEX_SYNC_INTERVALS_HOURS}
    return by_text.get((text or "").strip())


def get_swisseldex_schedule(db_path: Path, addon_auto_sync: bool, addon_interval_hours: int) -> SwisseldexSchedule:
    """The automatic-sync schedule in effect: as saved on "Externe Daten", else the add-on options.

    A stored interval that is not one of the offered ones (hand-edited
    database) keeps the add-on interval instead of guessing.
    """
    stored_auto = get_setting(db_path, SWISSELDEX_AUTO_SYNC_KEY)
    if stored_auto not in ("0", "1"):
        return SwisseldexSchedule(addon_auto_sync, addon_interval_hours, from_addon_options=True)
    stored_interval = get_setting(db_path, SWISSELDEX_INTERVAL_KEY)
    interval = swisseldex_interval_from_text(stored_interval)
    if interval is None:
        logger.warning("Stored Swisseldex interval %r is not one of %s, using %d h",
                       stored_interval, SWISSELDEX_SYNC_INTERVALS_HOURS, addon_interval_hours)
        interval = addon_interval_hours
    return SwisseldexSchedule(stored_auto == "1", interval)


def save_swisseldex_schedule(db_path: Path, auto_sync: bool, interval_hours: int) -> SwisseldexSchedule:
    """Store on/off and interval together; from now on they win over the add-on options."""
    if interval_hours not in SWISSELDEX_SYNC_INTERVALS_HOURS:
        raise ValueError(f"interval_hours={interval_hours} is not one of {SWISSELDEX_SYNC_INTERVALS_HOURS}")
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            _upsert(conn, community_row_id, SWISSELDEX_AUTO_SYNC_KEY, "1" if auto_sync else "0", now)
            _upsert(conn, community_row_id, SWISSELDEX_INTERVAL_KEY, str(interval_hours), now)
    return SwisseldexSchedule(auto_sync, interval_hours)
