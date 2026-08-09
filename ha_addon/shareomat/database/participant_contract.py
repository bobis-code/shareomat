# -*- coding: utf-8 -*-
"""
File: shareomat/database/participant_contract.py

Purpose:
    Assigns a participant to a specific contract version (and, at that
    point in time, a specific tariff) — so it stays reconstructable later
    which conditions applied to a participant during a given period, even
    after the community activates a newer contract version or tariff.
    Reuses the existing tariffs table by id rather than duplicating rate
    data here.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    accepted_at and notified_at answer two different legal questions and
    must never be conflated: accepted_at is the participant's original,
    immutable join confirmation (LEG-Mustervertrag's Beitrittserklärung) —
    it is never touched again once set. notified_at tracks whether the
    participant has been informed of the *currently* assigned contract
    version — per LEG-Mustervertrag §8, an existing participant only needs
    to be notified of a contract amendment, not to sign a new declaration.
    shareomat.database.contract_versions.publish_version() carries
    accepted_at forward unchanged when an assignment moves to a new
    version, but always resets notified_at to NULL there.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date


class ParticipantContractError(Exception):
    """Raised when assigning a contract to an unknown participant, or acting on an unknown assignment."""


@dataclass
class ParticipantContractAssignment:
    """One participant's acceptance of one contract version, with the tariff that applied."""

    participant_id: str        # business key (Participant.participant_id), not the row id
    contract_version_id: int
    tariff_id: int | None
    joined_at: date | None
    left_at: date | None
    accepted_at: date | None
    notified_at: date | None
    created_at: datetime
    id: int | None = None


def _row_to_assignment(row: sqlite3.Row, participant_id: str) -> ParticipantContractAssignment:
    return ParticipantContractAssignment(
        participant_id=participant_id,
        contract_version_id=row["contract_version_id"],
        tariff_id=row["tariff_id"],
        joined_at=str_to_date(row["joined_at"]),
        left_at=str_to_date(row["left_at"]),
        accepted_at=str_to_date(row["accepted_at"]),
        notified_at=str_to_date(row["notified_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        id=row["id"],
    )


def assign_contract(
    db_path: Path, participant_id: str, contract_version_id: int, tariff_id: int | None = None, *,
    joined_at: date | None = None, accepted_at: date | None = None,
) -> ParticipantContractAssignment:
    """Record that `participant_id` accepted `contract_version_id` (idempotent per pair — upsert)."""
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            participant_row = conn.execute(
                "SELECT id FROM participants WHERE participant_id = ?", (participant_id,),
            ).fetchone()
            if participant_row is None:
                raise ParticipantContractError(f"Teilnehmer '{participant_id}' wurde nicht gefunden.")

            conn.execute(
                """
                INSERT INTO participant_contract
                    (participant_id, contract_version_id, tariff_id, joined_at, left_at, accepted_at, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(participant_id, contract_version_id)
                DO UPDATE SET tariff_id = excluded.tariff_id, joined_at = excluded.joined_at,
                              accepted_at = excluded.accepted_at
                """,
                (
                    participant_row["id"], contract_version_id, tariff_id,
                    date_to_str(joined_at), None, date_to_str(accepted_at), now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM participant_contract WHERE participant_id = ? AND contract_version_id = ?",
                (participant_row["id"], contract_version_id),
            ).fetchone()
    return _row_to_assignment(row, participant_id)


def list_assignments_for_version(db_path: Path, contract_version_id: int) -> list[ParticipantContractAssignment]:
    """Return every participant assignment for one contract version."""
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT pc.*, p.participant_id AS participant_business_id
            FROM participant_contract pc
            JOIN participants p ON p.id = pc.participant_id
            WHERE pc.contract_version_id = ?
            ORDER BY p.participant_id
            """,
            (contract_version_id,),
        ).fetchall()
        return [_row_to_assignment(r, r["participant_business_id"]) for r in rows]


def get_assignment(db_path: Path, assignment_id: int) -> ParticipantContractAssignment | None:
    """Return one assignment by its internal database id, or None if it does not exist."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT pc.*, p.participant_id AS participant_business_id
            FROM participant_contract pc
            JOIN participants p ON p.id = pc.participant_id
            WHERE pc.id = ?
            """,
            (assignment_id,),
        ).fetchone()
        return _row_to_assignment(row, row["participant_business_id"]) if row else None


def mark_accepted(db_path: Path, assignment_id: int, accepted_at: date) -> ParticipantContractAssignment:
    """Confirm a brand-new participant's Beitrittserklärung — sets accepted_at AND notified_at.

    Confirming the join declaration for the current version inherently
    means the participant has also been informed of that version, so both
    fields are set together here. For an existing participant carried
    forward to a newer version (accepted_at already set), use
    mark_notified() instead — accepted_at must not be re-set.
    """
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE participant_contract SET accepted_at = ?, notified_at = ? WHERE id = ?",
                (date_to_str(accepted_at), date_to_str(accepted_at), assignment_id),
            )
            if cursor.rowcount == 0:
                raise ParticipantContractError(f"Zuordnung {assignment_id} wurde nicht gefunden.")
    result = get_assignment(db_path, assignment_id)
    assert result is not None
    return result


def mark_notified(db_path: Path, assignment_id: int, notified_at: date) -> ParticipantContractAssignment:
    """Record that a participant has been informed of the currently assigned contract version.

    Only sets notified_at — accepted_at (the original join confirmation)
    is left untouched, matching LEG-Mustervertrag §8: an existing
    participant only needs notice of a contract amendment, not a new
    signature.
    """
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE participant_contract SET notified_at = ? WHERE id = ?",
                (date_to_str(notified_at), assignment_id),
            )
            if cursor.rowcount == 0:
                raise ParticipantContractError(f"Zuordnung {assignment_id} wurde nicht gefunden.")
    result = get_assignment(db_path, assignment_id)
    assert result is not None
    return result


def record_departure(db_path: Path, assignment_id: int, left_at: date) -> ParticipantContractAssignment:
    """Record a participant's departure on their contract assignment. Deletes nothing."""
    with connect(db_path) as conn:
        with conn:
            cursor = conn.execute(
                "UPDATE participant_contract SET left_at = ? WHERE id = ?",
                (date_to_str(left_at), assignment_id),
            )
            if cursor.rowcount == 0:
                raise ParticipantContractError(f"Zuordnung {assignment_id} wurde nicht gefunden.")
    result = get_assignment(db_path, assignment_id)
    assert result is not None
    return result


def has_any_assignment(db_path: Path) -> bool:
    """True if at least one participant has ever been assigned to any contract version.

    Used as a heuristic for "is this the community's very first-ever
    participant assignment" (LEG founding) to pick the 3-month rather than
    1-month grid-operator notice-period warning when adding a participant.
    """
    with connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM participant_contract LIMIT 1").fetchone()
        return row is not None
