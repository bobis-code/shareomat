# -*- coding: utf-8 -*-
"""
File: shareomat/database/meters.py

Purpose:
    Read and write meters (master data) in SQLite, including assignment
    to a participant.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    meter_id is the business key the settlement core already uses; the
    internal FK to participants (participant_id column, integer) is
    resolved from the participant's business id (Meter.participant_id,
    string) on write and reversed on read.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.models.meter import Meter


def _row_to_meter(row: sqlite3.Row, participant_business_id: str) -> Meter:
    return Meter(
        meter_id=row["meter_id"],
        participant_id=participant_business_id,
        label=row["label"],
        role=row["role"],
        valid_from=str_to_date(row["valid_from"]),
        valid_until=str_to_date(row["valid_until"]),
        active=bool(row["active"]),
    )


_SELECT_WITH_PARTICIPANT = """
    SELECT meters.*, participants.participant_id AS participant_business_id
    FROM meters
    LEFT JOIN participants ON participants.id = meters.participant_id
"""


def list_meters(db_path: Path, *, include_inactive: bool = True) -> list[Meter]:
    """Return all meters, ordered by label. Set include_inactive=False to hide deactivated ones."""
    query = _SELECT_WITH_PARTICIPANT
    if not include_inactive:
        query += " WHERE meters.active = 1"
    query += " ORDER BY meters.label"
    with connect(db_path) as conn:
        rows = conn.execute(query).fetchall()
        return [_row_to_meter(r, r["participant_business_id"] or "") for r in rows]


def get_meter(db_path: Path, meter_id: str) -> Meter | None:
    """Return one meter by business id, or None if it does not exist."""
    with connect(db_path) as conn:
        row = conn.execute(
            _SELECT_WITH_PARTICIPANT + " WHERE meters.meter_id = ?", (meter_id,)
        ).fetchone()
        return _row_to_meter(row, row["participant_business_id"] or "") if row else None


def _resolve_participant_row_id(conn: sqlite3.Connection, participant_id: str) -> int:
    row = conn.execute(
        "SELECT id FROM participants WHERE participant_id = ?", (participant_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Teilnehmer '{participant_id}' wurde nicht gefunden.")
    return row["id"]


def create_meter(db_path: Path, meter: Meter) -> Meter:
    """Insert a new meter, assigned to an existing participant. Raises ValueError on conflict."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            participant_row_id = _resolve_participant_row_id(conn, meter.participant_id)
            existing = conn.execute(
                "SELECT 1 FROM meters WHERE community_id = ? AND meter_id = ?",
                (community_row_id, meter.meter_id),
            ).fetchone()
            if existing:
                raise ValueError(f"Messpunkt '{meter.meter_id}' existiert bereits.")
            conn.execute(
                """
                INSERT INTO meters
                    (community_id, participant_id, meter_id, label, role,
                     valid_from, valid_until, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    community_row_id, participant_row_id, meter.meter_id, meter.label,
                    meter.role, date_to_str(meter.valid_from), date_to_str(meter.valid_until),
                    int(meter.active), now, now,
                ),
            )
    result = get_meter(db_path, meter.meter_id)
    assert result is not None
    return result


def _guard_reassignment(conn, meter_id: str, old_participant_row_id, new_participant_row_id) -> None:
    """Refuse moving an already-billed meter to another participant.

    Meter -> participant is not historised yet, so a re-computation of an
    earlier period would credit the new participant with the old one's
    energy. Until a dated assignment history exists, a meter that appears
    in a released billing run keeps its participant (end it and register
    the successor's meter data under a new assignment instead).
    """
    if old_participant_row_id == new_participant_row_id:
        return
    billed = conn.execute(
        """
        SELECT 1 FROM billing_records br
        JOIN billing_runs r ON r.id = br.billing_run_id
        WHERE r.status = 'released' AND (',' || br.meter_ids || ',') LIKE ?
        LIMIT 1
        """,
        (f"%,{meter_id},%",),
    ).fetchone()
    if billed:
        raise ValueError(
            f"Messpunkt '{meter_id}' ist bereits in einer freigegebenen Abrechnung enthalten und kann nicht "
            "einem anderen Teilnehmer zugeordnet werden — frühere Abrechnungen würden sonst dem neuen "
            "Teilnehmer zugerechnet. Ein Zuordnungswechsel per Stichtag wird noch nicht unterstützt."
        )


def update_meter(db_path: Path, meter_id: str, meter: Meter) -> Meter:
    """Update an existing meter by business id, including its participant assignment."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            existing = conn.execute(
                "SELECT id, participant_id FROM meters WHERE meter_id = ?", (meter_id,)
            ).fetchone()
            if not existing:
                raise ValueError(f"Messpunkt '{meter_id}' wurde nicht gefunden.")
            participant_row_id = _resolve_participant_row_id(conn, meter.participant_id)
            _guard_reassignment(conn, meter_id, existing["participant_id"], participant_row_id)
            conn.execute(
                """
                UPDATE meters
                SET meter_id=?, participant_id=?, label=?, role=?, valid_from=?, valid_until=?,
                    active=?, updated_at=?
                WHERE id=?
                """,
                (
                    meter.meter_id, participant_row_id, meter.label, meter.role,
                    date_to_str(meter.valid_from), date_to_str(meter.valid_until),
                    int(meter.active), now, existing["id"],
                ),
            )
    result = get_meter(db_path, meter.meter_id)
    assert result is not None
    return result


def assign_meter_to_participant(db_path: Path, meter_id: str, participant_id: str) -> Meter:
    """Reassign an existing meter to a different participant."""
    with connect(db_path) as conn:
        with conn:
            participant_row_id = _resolve_participant_row_id(conn, participant_id)
            current = conn.execute("SELECT participant_id FROM meters WHERE meter_id = ?", (meter_id,)).fetchone()
            if current is not None:
                _guard_reassignment(conn, meter_id, current["participant_id"], participant_row_id)
            cursor = conn.execute(
                "UPDATE meters SET participant_id=?, updated_at=? WHERE meter_id=?",
                (participant_row_id, now_iso(), meter_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Messpunkt '{meter_id}' wurde nicht gefunden.")
    result = get_meter(db_path, meter_id)
    assert result is not None
    return result


def set_meter_active(db_path: Path, meter_id: str, active: bool) -> None:
    """Activate or deactivate a meter without touching its other fields."""
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE meters SET active=?, updated_at=? WHERE meter_id=?",
                (int(active), now_iso(), meter_id),
            )
            if cursor.rowcount == 0:
                raise ValueError(f"Messpunkt '{meter_id}' wurde nicht gefunden.")
