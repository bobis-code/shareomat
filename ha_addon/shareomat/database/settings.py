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
"""

from __future__ import annotations

from dataclasses import asdict, fields
from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, now_iso
from shareomat.models.settings import OperationSettings

# bool/int/str fields on OperationSettings, in the order they are (de)serialized.
_BOOL_FIELDS = {"archive_processed", "auto_scan_enabled", "auto_create_billing",
                "auto_create_invoices", "auto_send_invoices"}
_INT_FIELDS = {"scan_interval_seconds"}


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
            community_row_id = ensure_community_row_id(conn)
            conn.execute(
                """
                INSERT INTO settings (community_id, setting_key, setting_value, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(community_id, setting_key)
                DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
                """,
                (community_row_id, key, value, now),
            )


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
                conn.execute(
                    """
                    INSERT INTO settings (community_id, setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(community_id, setting_key)
                    DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
                    """,
                    (community_row_id, key, stored_value, now),
                )
    return get_operation_settings(db_path)
