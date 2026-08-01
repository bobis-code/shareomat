# -*- coding: utf-8 -*-
"""
File: shareomat/database/community.py

Purpose:
    Read and write the single LEG/ZEV community row in SQLite.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The schema allows multiple communities rows, but the settlement core
    and this admin UI only ever operate on one — the first row by id.
    ensure_community_row_id() is the internal helper other database
    modules (participants/meters/tariffs) use to resolve the community
    foreign key, auto-provisioning an empty community row if the setup
    wizard has not created one yet.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso
from shareomat.models.community import Community


def _row_to_community(row: sqlite3.Row) -> Community:
    return Community(
        community_id=row["community_id"],
        name=row["name"],
        address_line=row["address_line"],
        postal_code=row["postal_code"],
        city=row["city"],
        active=bool(row["active"]),
    )


def get_community(db_path: Path) -> Community | None:
    """Return the community, or None if none has been created yet (fresh install)."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM communities ORDER BY id LIMIT 1").fetchone()
        return _row_to_community(row) if row else None


def save_community(db_path: Path, community: Community) -> Community:
    """Create or update the (single) community row."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            existing = conn.execute("SELECT id FROM communities ORDER BY id LIMIT 1").fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE communities
                    SET community_id=?, name=?, address_line=?, postal_code=?, city=?,
                        active=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        community.community_id, community.name, community.address_line,
                        community.postal_code, community.city, int(community.active),
                        now, existing["id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO communities
                        (community_id, name, address_line, postal_code, city, active, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        community.community_id, community.name, community.address_line,
                        community.postal_code, community.city, int(community.active),
                        now, now,
                    ),
                )
    result = get_community(db_path)
    assert result is not None
    return result


def ensure_community_row_id(conn: sqlite3.Connection) -> int:
    """Return the internal id of the single community row, creating an empty one if missing.

    Used by participants/meters/tariffs CRUD so they can insert rows even
    before the setup wizard's "Gemeinschaft" step has run.
    """
    row = conn.execute("SELECT id FROM communities ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    now = now_iso()
    cursor = conn.execute(
        """
        INSERT INTO communities (community_id, name, address_line, postal_code, city, active, created_at, updated_at)
        VALUES ('', '', '', '', '', 1, ?, ?)
        """,
        (now, now),
    )
    return cursor.lastrowid
