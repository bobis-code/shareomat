# -*- coding: utf-8 -*-
"""
File: shareomat/database/external_settings.py

Purpose:
    Read and write settings for external data sources (currently: the
    ENTSO-E API token). Reuses the same generic `settings` key/value
    table as shareomat.database.settings — no new table needed.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The ENTSO-E token is entered through the web UI and stored in
    SQLite, never in the technical runtime YAML — it is Shareomat
    business data (a specific external-data feature), not add-on
    infrastructure configuration.
"""

from __future__ import annotations

from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, now_iso
from shareomat.models.external_data import ExternalDataSettings

_KEY_ENTSOE_TOKEN = "entsoe_api_token"


def get_external_data_settings(db_path: Path) -> ExternalDataSettings:
    """Return the stored external-data settings, defaulting to an empty token if unset."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT setting_value FROM settings WHERE setting_key = ?", (_KEY_ENTSOE_TOKEN,)
        ).fetchone()
    return ExternalDataSettings(entsoe_api_token=row["setting_value"] if row else "")


def save_external_data_settings(db_path: Path, settings: ExternalDataSettings) -> ExternalDataSettings:
    """Persist the external-data settings."""
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
                (community_row_id, _KEY_ENTSOE_TOKEN, settings.entsoe_api_token, now),
            )
    return get_external_data_settings(db_path)
