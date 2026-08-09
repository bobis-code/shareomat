# -*- coding: utf-8 -*-
"""
File: shareomat/database/contract_settings.py

Purpose:
    Read and write the LEG contract master data (representative, grid
    operator, notice/distribution rules). Reuses the same generic
    `settings` key/value table as shareomat.database.settings/
    external_settings.py — no new table needed, same reasoning as those
    two modules (single row per community, no versioning of its own —
    versioning lives in shareomat.database.contract_versions instead).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, now_iso
from shareomat.models.contract import ContractSettings

_KEY_REPRESENTATIVE_NAME = "contract_representative_name"
_KEY_REPRESENTATIVE_ADDRESS_LINE = "contract_representative_address_line"
_KEY_REPRESENTATIVE_POSTAL_CODE = "contract_representative_postal_code"
_KEY_REPRESENTATIVE_CITY = "contract_representative_city"
_KEY_REPRESENTATIVE_EMAIL = "contract_representative_email"
_KEY_GRID_OPERATOR = "contract_grid_operator"
_KEY_DISTRIBUTION_METHOD = "contract_distribution_method"

_ALL_KEYS = (
    _KEY_REPRESENTATIVE_NAME, _KEY_REPRESENTATIVE_ADDRESS_LINE, _KEY_REPRESENTATIVE_POSTAL_CODE,
    _KEY_REPRESENTATIVE_CITY, _KEY_REPRESENTATIVE_EMAIL, _KEY_GRID_OPERATOR,
    _KEY_DISTRIBUTION_METHOD,
)


def get_contract_settings(db_path: Path) -> ContractSettings:
    """Return the stored contract master data, falling back to defaults for unset keys."""
    defaults = ContractSettings()
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT setting_key, setting_value FROM settings WHERE setting_key IN "
            f"({','.join('?' * len(_ALL_KEYS))})",
            _ALL_KEYS,
        ).fetchall()
    stored = {r["setting_key"]: r["setting_value"] for r in rows}
    return ContractSettings(
        representative_name=stored.get(_KEY_REPRESENTATIVE_NAME, defaults.representative_name),
        representative_address_line=stored.get(
            _KEY_REPRESENTATIVE_ADDRESS_LINE, defaults.representative_address_line),
        representative_postal_code=stored.get(
            _KEY_REPRESENTATIVE_POSTAL_CODE, defaults.representative_postal_code),
        representative_city=stored.get(_KEY_REPRESENTATIVE_CITY, defaults.representative_city),
        representative_email=stored.get(_KEY_REPRESENTATIVE_EMAIL, defaults.representative_email),
        grid_operator=stored.get(_KEY_GRID_OPERATOR, defaults.grid_operator),
        distribution_method=stored.get(_KEY_DISTRIBUTION_METHOD, defaults.distribution_method),
    )


def save_contract_settings(db_path: Path, settings: ContractSettings) -> ContractSettings:
    """Persist the contract master data."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            for key, value in (
                (_KEY_REPRESENTATIVE_NAME, settings.representative_name),
                (_KEY_REPRESENTATIVE_ADDRESS_LINE, settings.representative_address_line),
                (_KEY_REPRESENTATIVE_POSTAL_CODE, settings.representative_postal_code),
                (_KEY_REPRESENTATIVE_CITY, settings.representative_city),
                (_KEY_REPRESENTATIVE_EMAIL, settings.representative_email),
                (_KEY_GRID_OPERATOR, settings.grid_operator),
                (_KEY_DISTRIBUTION_METHOD, settings.distribution_method),
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
    return get_contract_settings(db_path)
