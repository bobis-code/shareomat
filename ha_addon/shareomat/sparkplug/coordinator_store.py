# -*- coding: utf-8 -*-
"""
File: shareomat/sparkplug/coordinator_store.py

Purpose:
    coordinator.db: the demand forecasts and day-ahead prices that only serve
    the LEG data sent to Emsomat, kept apart from shareomat.db.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    A release of a billing run refuses if PRAGMA data_version of shareomat.db
    changed while it was checking, and a commit to any table counts. Different
    tables are therefore not enough: these writes need their own file.

    The table definitions are the existing ones from database/sqlite.py, and
    the existing database functions are used with this path. Data here can be
    recomputed at any time, so nothing is copied over from shareomat.db.
"""

from __future__ import annotations

import os
from pathlib import Path

from shareomat.database.sqlite import _SCHEMA_STATEMENTS, connect

_COORDINATOR_TABLES = ("consumption_forecasts", "day_ahead_prices")


class CoordinatorStoreError(RuntimeError):
    """The coordinator data would end up in the billing database."""


def coordinator_db_path(db_path: Path) -> Path:
    """coordinator.db lives next to shareomat.db, in the same persistent data directory.

    Never the same name, even if the main database is configured as coordinator.db.
    """
    name = "coordinator.db"
    if db_path.name.casefold() == name:
        name = "coordinator-data.db"
    return db_path.with_name(name)


def ensure_separate_from(db_path: Path, store: Path) -> None:
    """Refuse a store that is really the main database, for example through a link."""
    same = os.path.normcase(store.resolve()) == os.path.normcase(db_path.resolve())
    if not same and store.exists() and db_path.exists():
        same = os.path.samefile(store, db_path)
    if same:
        raise CoordinatorStoreError(f"{store} is the billing database; coordinator data is not written")


def init_coordinator_db(path: Path) -> None:
    """Create the two tables and their indexes if missing. Idempotent."""
    statements = [s for s in _SCHEMA_STATEMENTS if any(table in s for table in _COORDINATOR_TABLES)]
    with connect(path) as conn:
        with conn:
            for statement in statements:
                conn.execute(statement)


__all__ = ["CoordinatorStoreError", "coordinator_db_path", "ensure_separate_from", "init_coordinator_db"]
