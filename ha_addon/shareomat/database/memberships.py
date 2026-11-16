# -*- coding: utf-8 -*-
"""
File: shareomat/database/memberships.py

Purpose:
    Read access to memberships (ADR-0009) and the operator's confirmation
    of memberships the schema-18 migration flagged for review.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Writing join, departure and grid-operator data goes through
    shareomat.database.participant_contract (record/correct functions), so
    there is exactly one place that changes a membership.

    A membership with a review_note blocks every billing preview
    (shareomat.database.billing) until the operator confirms its values —
    conflicting data from before schema 18 is never silently trusted.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from shareomat.database.audit import record_event
from shareomat.database.sqlite import connect, now_iso, str_to_date
from shareomat.models.membership import Membership


class MembershipError(Exception):
    """Raised for an unknown membership or an action that does not apply to it."""


# ── Private helpers ─────────────────────────────────────────────────────────


def _row_to_membership(row: sqlite3.Row) -> Membership:
    return Membership(
        participant_id=row["participant_business_id"],
        joined_at=str_to_date(row["joined_at"]),
        left_at=str_to_date(row["left_at"]),
        exit_reason=row["exit_reason"],
        vnb_notified_at=str_to_date(row["vnb_notified_at"]),
        vnb_confirmed_at=str_to_date(row["vnb_confirmed_at"]),
        effective_start_at=str_to_date(row["effective_start_at"]),
        vnb_notified_paperless_id=row["vnb_notified_paperless_id"],
        vnb_notified_paperless_asn=row["vnb_notified_paperless_asn"],
        vnb_confirmed_paperless_id=row["vnb_confirmed_paperless_id"],
        vnb_confirmed_paperless_asn=row["vnb_confirmed_paperless_asn"],
        review_note=row["review_note"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        id=row["id"],
    )


_SELECT = """
    SELECT m.*, p.participant_id AS participant_business_id
    FROM memberships m JOIN participants p ON p.id = m.participant_id
"""

# ── Public functions ────────────────────────────────────────────────────────


def list_memberships(db_path: Path, participant_id: str | None = None) -> list[Membership]:
    """All memberships (of one participant), oldest join first."""
    query = _SELECT + (" WHERE p.participant_id = ?" if participant_id is not None else "")
    params = (participant_id,) if participant_id is not None else ()
    with connect(db_path) as conn:
        rows = conn.execute(query + " ORDER BY p.participant_id, m.joined_at, m.id", params).fetchall()
    return [_row_to_membership(r) for r in rows]


def get_membership(db_path: Path, membership_id: int) -> Membership | None:
    with connect(db_path) as conn:
        row = conn.execute(_SELECT + " WHERE m.id = ?", (membership_id,)).fetchone()
    return _row_to_membership(row) if row else None


def memberships_needing_review(db_path: Path) -> list[Membership]:
    """Memberships the schema-18 migration flagged and the operator has not confirmed yet."""
    with connect(db_path) as conn:
        rows = conn.execute(_SELECT + " WHERE m.review_note != '' ORDER BY p.participant_id, m.id").fetchall()
    return [_row_to_membership(r) for r in rows]


def confirm_review(db_path: Path, membership_id: int) -> Membership:
    """The operator checked the flagged values (and corrected them where needed): unblock billing.

    The review note goes into the audit log unchanged, so it stays
    traceable what was found and when it was cleared.
    """
    with connect(db_path) as conn:
        with conn:
            row = conn.execute(_SELECT + " WHERE m.id = ?", (membership_id,)).fetchone()
            if row is None:
                raise MembershipError(f"Mitgliedschaft {membership_id} wurde nicht gefunden.")
            if not row["review_note"]:
                raise MembershipError("Für diese Mitgliedschaft ist keine Prüfung offen.")
            conn.execute("UPDATE memberships SET review_note = '', updated_at = ? WHERE id = ?",
                         (now_iso(), membership_id))
            record_event(conn, "participant", row["participant_business_id"], "membership_review_confirmed",
                         f"Mitgliedschaft {membership_id} geprüft: {row['review_note']}")
    result = get_membership(db_path, membership_id)
    assert result is not None
    return result
