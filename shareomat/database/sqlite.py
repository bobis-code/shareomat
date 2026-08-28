# -*- coding: utf-8 -*-
"""
File: shareomat/database/sqlite.py

Purpose:
    SQLite connection handling, schema creation, and migrations for the
    Shareomat administrative database.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Every call opens and closes its own short-lived connection — the
    database is shared between the settlement engine thread and the web
    server's request threads, and SQLite connections must not be shared
    across threads.

    Money values (tariff rates) are stored as TEXT (decimal string), never
    as REAL, so round-tripping through the database never loses precision
    to float rounding.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/shareomat.db")

_SCHEMA_VERSION = 12

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS communities (
        id INTEGER PRIMARY KEY,
        community_id TEXT NOT NULL UNIQUE,
        name TEXT NOT NULL,
        address_line TEXT NOT NULL DEFAULT '',
        postal_code TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '',
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS participants (
        id INTEGER PRIMARY KEY,
        community_id INTEGER NOT NULL REFERENCES communities(id),
        participant_id TEXT NOT NULL,
        label TEXT NOT NULL,
        participant_type TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        address_line TEXT NOT NULL DEFAULT '',
        postal_code TEXT NOT NULL DEFAULT '',
        city TEXT NOT NULL DEFAULT '',
        valid_from TEXT,
        valid_until TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(community_id, participant_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meters (
        id INTEGER PRIMARY KEY,
        community_id INTEGER NOT NULL REFERENCES communities(id),
        participant_id INTEGER REFERENCES participants(id),
        meter_id TEXT NOT NULL,
        label TEXT NOT NULL,
        role TEXT NOT NULL,
        valid_from TEXT,
        valid_until TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(community_id, meter_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tariffs (
        id INTEGER PRIMARY KEY,
        community_id INTEGER NOT NULL REFERENCES communities(id),
        name TEXT NOT NULL DEFAULT 'Standard',
        local_rate_chf_kwh TEXT NOT NULL,
        grid_rate_chf_kwh TEXT NOT NULL,
        feed_in_rate_chf_kwh TEXT NOT NULL,
        valid_from TEXT NOT NULL,
        valid_until TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS settings (
        id INTEGER PRIMARY KEY,
        community_id INTEGER REFERENCES communities(id),
        setting_key TEXT NOT NULL,
        setting_value TEXT,
        updated_at TEXT NOT NULL,
        UNIQUE(community_id, setting_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS supplier_tariffs (
        id INTEGER PRIMARY KEY,
        community_id INTEGER NOT NULL REFERENCES communities(id),
        source TEXT NOT NULL,
        energy_rate_ht_chf_kwh TEXT,
        energy_rate_nt_chf_kwh TEXT,
        grid_rate_ht_chf_kwh TEXT,
        grid_rate_nt_chf_kwh TEXT,
        base_price_chf_year TEXT,
        metering_price_chf_year TEXT,
        feed_in_rate_chf_kwh TEXT,
        hkn_rate_chf_kwh TEXT,
        valid_from TEXT NOT NULL,
        valid_until TEXT,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS reference_prices (
        id INTEGER PRIMARY KEY,
        technology TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        price_chf_kwh TEXT NOT NULL,
        source TEXT NOT NULL,
        is_official INTEGER NOT NULL DEFAULT 1,
        published_at TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(technology, period_start, source)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS price_forecasts (
        id INTEGER PRIMARY KEY,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        forecast_price_chf_kwh TEXT NOT NULL,
        completeness_pct TEXT,
        sources TEXT NOT NULL,
        computed_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS exchange_rates (
        id INTEGER PRIMARY KEY,
        currency TEXT NOT NULL,
        period TEXT NOT NULL,
        rate_chf TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'SNB',
        fetched_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(currency, period)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS elcom_tariffs (
        id INTEGER PRIMARY KEY,
        municipality_bfs_number TEXT NOT NULL,
        year INTEGER NOT NULL,
        category TEXT NOT NULL,
        energy_chf_kwh TEXT NOT NULL,
        grid_chf_kwh TEXT NOT NULL,
        aidfee_chf_kwh TEXT NOT NULL,
        community_fees_chf_kwh TEXT NOT NULL,
        total_chf_kwh TEXT NOT NULL,
        fixcosts_chf_year TEXT NOT NULL,
        operator_name TEXT NOT NULL DEFAULT '',
        fetched_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(municipality_bfs_number, year, category)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS data_imports (
        id INTEGER PRIMARY KEY,
        source TEXT NOT NULL,
        data_type TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        valid_from TEXT,
        status TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        checksum TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS billing_periods (
        id INTEGER PRIMARY KEY,
        community_id INTEGER NOT NULL REFERENCES communities(id),
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(community_id, period_start, period_end)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS billing_runs (
        id INTEGER PRIMARY KEY,
        billing_period_id INTEGER NOT NULL REFERENCES billing_periods(id),
        version INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        community_id TEXT NOT NULL,
        community_name TEXT NOT NULL,
        tariff_name TEXT NOT NULL,
        local_rate_chf_kwh TEXT NOT NULL,
        grid_rate_chf_kwh TEXT NOT NULL,
        feed_in_rate_chf_kwh TEXT NOT NULL,
        participant_count INTEGER NOT NULL DEFAULT 0,
        total_local_kwh TEXT NOT NULL DEFAULT '0',
        total_leg_amount_chf TEXT NOT NULL DEFAULT '0',
        total_grid_kwh TEXT NOT NULL DEFAULT '0',
        total_grid_amount_chf TEXT NOT NULL DEFAULT '0',
        computed_at TEXT NOT NULL,
        released_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(billing_period_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS billing_records (
        id INTEGER PRIMARY KEY,
        billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id),
        participant_id TEXT NOT NULL,
        participant_label TEXT NOT NULL,
        meter_ids TEXT NOT NULL DEFAULT '',
        local_received_kwh TEXT NOT NULL DEFAULT '0',
        local_amount_chf TEXT NOT NULL DEFAULT '0',
        grid_import_kwh TEXT NOT NULL DEFAULT '0',
        grid_amount_chf TEXT NOT NULL DEFAULT '0',
        local_supplied_kwh TEXT NOT NULL DEFAULT '0',
        grid_export_kwh TEXT NOT NULL DEFAULT '0',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS billing_sources (
        id INTEGER PRIMARY KEY,
        billing_run_id INTEGER NOT NULL REFERENCES billing_runs(id),
        filename TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        origin TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS meter_readings (
        id INTEGER PRIMARY KEY,
        meter_id TEXT NOT NULL,
        slot_start TEXT NOT NULL,
        direction TEXT NOT NULL,
        value_kwh REAL NOT NULL,
        quality TEXT NOT NULL DEFAULT 'valid',
        source_file TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL,
        UNIQUE(meter_id, slot_start, direction)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_meter_readings_slot ON meter_readings(slot_start)
    """,
    """
    CREATE TABLE IF NOT EXISTS settlement_cycle_records (
        id INTEGER PRIMARY KEY,
        run_id TEXT NOT NULL,
        period_start TEXT NOT NULL,
        period_end TEXT NOT NULL,
        participant_id TEXT NOT NULL,
        participant_label TEXT NOT NULL,
        meter_ids TEXT NOT NULL DEFAULT '',
        source_fingerprint TEXT NOT NULL,
        local_received_kwh REAL NOT NULL DEFAULT 0,
        grid_import_kwh REAL NOT NULL DEFAULT 0,
        local_supplied_kwh REAL NOT NULL DEFAULT 0,
        grid_export_kwh REAL NOT NULL DEFAULT 0,
        local_cost_chf TEXT NOT NULL DEFAULT '0',
        grid_cost_chf TEXT NOT NULL DEFAULT '0',
        total_cost_chf TEXT NOT NULL DEFAULT '0',
        created_at TEXT NOT NULL,
        UNIQUE(participant_id, period_start, period_end, source_fingerprint)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_settlement_cycle_participant ON settlement_cycle_records(participant_id, period_start)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_settlement_cycle_run ON settlement_cycle_records(run_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS consumption_forecasts (
        id INTEGER PRIMARY KEY,
        scope TEXT NOT NULL DEFAULT 'leg',
        participant_id TEXT NOT NULL DEFAULT '',
        slot_start TEXT NOT NULL,
        forecast_kwh REAL,
        quality TEXT NOT NULL,
        method TEXT NOT NULL,
        sample_count INTEGER NOT NULL DEFAULT 0,
        data_period_start TEXT,
        data_period_end TEXT,
        computed_at TEXT NOT NULL,
        UNIQUE(scope, participant_id, slot_start)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_consumption_forecasts_slot ON consumption_forecasts(slot_start)
    """,
    """
    CREATE TABLE IF NOT EXISTS day_ahead_prices (
        id INTEGER PRIMARY KEY,
        slot_start TEXT NOT NULL UNIQUE,
        price_chf_kwh TEXT NOT NULL,
        computed_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_day_ahead_prices_slot ON day_ahead_prices(slot_start)
    """,
    """
    CREATE TABLE IF NOT EXISTS contract_versions (
        id INTEGER PRIMARY KEY,
        community_id INTEGER NOT NULL REFERENCES communities(id),
        version INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        contract_text_snapshot TEXT NOT NULL,
        valid_from TEXT,
        valid_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(community_id, version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS participant_contract (
        id INTEGER PRIMARY KEY,
        participant_id INTEGER NOT NULL REFERENCES participants(id),
        contract_version_id INTEGER NOT NULL REFERENCES contract_versions(id),
        tariff_id INTEGER REFERENCES tariffs(id),
        joined_at TEXT,
        left_at TEXT,
        accepted_at TEXT,
        created_at TEXT NOT NULL,
        UNIQUE(participant_id, contract_version_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    )
    """,
]

# Additive column migrations: table -> {column_name: DDL fragment}.
# CREATE TABLE IF NOT EXISTS above only helps brand-new installs — an
# existing database's tariffs table already exists, so that statement is a
# no-op for it and these columns would never appear without ALTER TABLE.
# SQLite's ALTER TABLE ADD COLUMN only supports one column at a time with a
# constant default, which is all these need.
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    "tariffs": {
        "admin_fee_chf_kwh": "TEXT NOT NULL DEFAULT '0'",
        "rate_mode": "TEXT NOT NULL DEFAULT 'flat'",
        "local_rate_nt_chf_kwh": "TEXT",
        "feed_in_rate_nt_chf_kwh": "TEXT",
    },
    "contract_versions": {
        "local_rate_chf_kwh": "TEXT NOT NULL DEFAULT '0'",
        "local_rate_nt_chf_kwh": "TEXT",
        "feed_in_rate_chf_kwh": "TEXT NOT NULL DEFAULT '0'",
        "feed_in_rate_nt_chf_kwh": "TEXT",
        "admin_fee_chf_kwh": "TEXT NOT NULL DEFAULT '0'",
        "rate_mode": "TEXT NOT NULL DEFAULT 'flat'",
        "representative_name": "TEXT NOT NULL DEFAULT ''",
        "representative_address_line": "TEXT NOT NULL DEFAULT ''",
        "representative_postal_code": "TEXT NOT NULL DEFAULT ''",
        "representative_city": "TEXT NOT NULL DEFAULT ''",
        "representative_email": "TEXT NOT NULL DEFAULT ''",
        "grid_operator": "TEXT NOT NULL DEFAULT ''",
        "distribution_method": "TEXT NOT NULL DEFAULT ''",
        "price_notice_period_months": "INTEGER NOT NULL DEFAULT 4",
        "contract_notice_period_months": "INTEGER NOT NULL DEFAULT 6",
        "tariff_id": "INTEGER REFERENCES tariffs(id)",
        "supersedes_version_id": "INTEGER REFERENCES contract_versions(id)",
        # Source figures the producer rate is derived from (see
        # shareomat.web.pages.contract._compute_rates): feed_in_rate_chf_kwh =
        # vnb_reference_price - price_reduction. Kept alongside the computed
        # feed_in_rate_chf_kwh/local_rate_chf_kwh so a historical version still
        # shows *why* the rate was what it was, not just the result.
        "vnb_reference_price_chf_kwh": "TEXT NOT NULL DEFAULT '0'",
        "price_reduction_chf_kwh": "TEXT NOT NULL DEFAULT '0'",
        "vnb_reference_price_nt_chf_kwh": "TEXT",
        "price_reduction_nt_chf_kwh": "TEXT",
    },
    "participant_contract": {
        # Separate from accepted_at: accepted_at is the participant's original,
        # immutable join confirmation (never touched again); notified_at tracks
        # whether they've been told about the CURRENT contract version — reset
        # to NULL whenever an assignment is carried forward to a new version at
        # publish time (see contract_versions.publish_version()).
        "notified_at": "TEXT",
    },
}


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """Add any columns from _COLUMN_MIGRATIONS missing on an existing table. Idempotent."""
    for table, columns in _COLUMN_MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def resolve_db_path() -> Path:
    """Return the configured database path (SHAREOMAT_DB_PATH env var, else the default)."""
    return Path(os.environ.get("SHAREOMAT_DB_PATH", str(DEFAULT_DB_PATH)))


def now_iso() -> str:
    """Return the current UTC timestamp as an ISO-8601 string for created_at/updated_at columns."""
    return datetime.now(timezone.utc).isoformat()


def date_to_str(value: date | None) -> str | None:
    """Serialize a date to 'YYYY-MM-DD' for storage, or None."""
    return value.isoformat() if value else None


def str_to_date(value: str | None) -> date | None:
    """Parse a 'YYYY-MM-DD' column value back into a date, or None."""
    return date.fromisoformat(value) if value else None


@contextmanager
def connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open a short-lived connection with the project's standard pragmas and row factory."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, timeout=5)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path) -> None:
    """Create tables if missing and record the current schema version. Idempotent."""
    with connect(db_path) as conn:
        with conn:
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            _ensure_columns(conn)
            applied = conn.execute(
                "SELECT 1 FROM schema_migrations WHERE version = ?", (_SCHEMA_VERSION,)
            ).fetchone()
            if applied is None:
                conn.execute(
                    "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                    (_SCHEMA_VERSION, now_iso()),
                )
                logger.info("Database schema initialized at %s (version %d)", db_path, _SCHEMA_VERSION)
