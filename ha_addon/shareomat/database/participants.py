# -*- coding: utf-8 -*-
"""
File: shareomat/database/participants.py

Purpose:
    Read and write participants (billing identities) in SQLite.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    participant_id is the business key the settlement core already uses
    (shareomat.core.pipeline.leg_billing, leg_matcher); it is unique per
    community, not globally in the table (see UNIQUE(community_id,
    participant_id) in shareomat.database.sqlite).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.models.participant import Participant


def _row_to_participant(row: sqlite3.Row) -> Participant:
    return Participant(
        participant_id=row["participant_id"],
        label=row["label"],
        participant_type=row["participant_type"],
        email=row["email"],
        address_line=row["address_line"],
        postal_code=row["postal_code"],
        city=row["city"],
        valid_from=str_to_date(row["valid_from"]),
        valid_until=str_to_date(row["valid_until"]),
        active=bool(row["active"]),
    )


def list_participants(db_path: Path, *, include_inactive: bool = True) -> list[Participant]:
    """Return all participants, ordered by label. Set include_inactive=False to hide deactivated ones."""
    query = "SELECT * FROM participants"
    if not include_inactive:
        query += " WHERE active = 1"
    query += " ORDER BY label"
    with connect(db_path) as conn:
        rows = conn.execute(query).fetchall()
        return [_row_to_participant(r) for r in rows]


def get_participant(db_path: Path, participant_id: str) -> Participant | None:
    """Return one participant by business id, or None if it does not exist."""
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM participants WHERE participant_id = ?", (participant_id,)
        ).fetchone()
        return _row_to_participant(row) if row else None


def create_participant(db_path: Path, participant: Participant) -> Participant:
    """Insert a new participant. Raises ValueError if the participant_id is already taken."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            existing = conn.execute(
                "SELECT 1 FROM participants WHERE community_id = ? AND participant_id = ?",
                (community_row_id, participant.participant_id),
            ).fetchone()
            if existing:
                raise ValueError(f"Teilnehmer '{participant.participant_id}' existiert bereits.")
            conn.execute(
                """
                INSERT INTO participants
                    (community_id, participant_id, label, participant_type, email,
                     address_line, postal_code, city, valid_from, valid_until, active,
                     created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    community_row_id, participant.participant_id, participant.label,
                    participant.participant_type, participant.email, participant.address_line,
                    participant.postal_code, participant.city,
                    date_to_str(participant.valid_from), date_to_str(participant.valid_until),
                    int(participant.active), now, now,
                ),
            )
    result = get_participant(db_path, participant.participant_id)
    assert result is not None
    return result


def update_participant(db_path: Path, participant_id: str, participant: Participant) -> Participant:
    """Update an existing participant by business id. Raises ValueError if it does not exist."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            existing = conn.execute(
                "SELECT id FROM participants WHERE participant_id = ?", (participant_id,)
            ).fetchone()
            if not existing:
                raise ValueError(f"Teilnehmer '{participant_id}' wurde nicht gefunden.")
            conn.execute(
                """
                UPDATE participants
                SET participant_id=?, label=?, participant_type=?, email=?, address_line=?,
                    postal_code=?, city=?, valid_from=?, valid_until=?, active=?, updated_at=?
                WHERE id=?
                """,
                (
                    participant.participant_id, participant.label, participant.participant_type,
                    participant.email, participant.address_line, participant.postal_code,
                    participant.city, date_to_str(participant.valid_from),
                    date_to_str(participant.valid_until), int(participant.active), now,
                    existing["id"],
                ),
            )
    result = get_participant(db_path, participant.participant_id)
    assert result is not None
    return result


def set_participant_active(db_path: Path, participant_id: str, active: bool) -> None:
    """Activate or deactivate a participant without touching its other fields."""
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE participants SET active=?, updated_at=? WHERE participant_id=?",
                (int(active), now_iso(), participant_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Teilnehmer '{participant_id}' wurde nicht gefunden.")
