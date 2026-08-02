# -*- coding: utf-8 -*-
"""
File: shareomat/database/external_settings.py

Purpose:
    Read and write settings for external data sources (ENTSO-E API token,
    default ElCom municipality). Reuses the same generic `settings`
    key/value table as shareomat.database.settings — no new table needed.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Entered through the web UI and stored in SQLite, never in the
    technical runtime YAML — this is Shareomat business data (a specific
    external-data feature), not add-on infrastructure configuration.
"""

from __future__ import annotations

from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, now_iso
from shareomat.models.external_data import ExternalDataSettings

_KEY_ENTSOE_TOKEN = "entsoe_api_token"
_KEY_MUNICIPALITY_BFS_NUMBER = "municipality_bfs_number"


def get_external_data_settings(db_path: Path) -> ExternalDataSettings:
    """Return the stored external-data settings, defaulting to empty values if unset."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT setting_key, setting_value FROM settings WHERE setting_key IN (?, ?)",
            (_KEY_ENTSOE_TOKEN, _KEY_MUNICIPALITY_BFS_NUMBER),
        ).fetchall()
    stored = {r["setting_key"]: r["setting_value"] for r in rows}
    return ExternalDataSettings(
        entsoe_api_token=stored.get(_KEY_ENTSOE_TOKEN, ""),
        municipality_bfs_number=stored.get(_KEY_MUNICIPALITY_BFS_NUMBER, ""),
    )


def save_external_data_settings(db_path: Path, settings: ExternalDataSettings) -> ExternalDataSettings:
    """Persist the external-data settings."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            for key, value in (
                (_KEY_ENTSOE_TOKEN, settings.entsoe_api_token),
                (_KEY_MUNICIPALITY_BFS_NUMBER, settings.municipality_bfs_number),
            ):
                conn.execute(
                    """
                    INSERT INTO settings (community_id, setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(community_id, setting_key)
                    DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
                    """,
                    (community_row_id, key, value, now),
                )
    return get_external_data_settings(db_path)
