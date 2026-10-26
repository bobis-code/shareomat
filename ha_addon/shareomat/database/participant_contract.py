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

    Evidence is write-once: a recorded acceptance, notification, grid-
    operator report or departure is never overwritten — a second attempt
    raises ParticipantContractError. Corrections need a new, logged event,
    not a silent update. accepted_version names the contract version that
    was actually signed (it stays the same when the row is carried to a
    later version). Every change is appended to audit_events.

    Status chain of a join: angelegt -> Erklärung ausgestellt
    (declaration_issued_at) -> Unterschrift eingegangen (accepted_at +
    signature evidence) -> beim Netzbetreiber gemeldet (vnb_notified_at)
    -> vom Netzbetreiber bestätigt (vnb_confirmed_at, effective_start_at)
    -> aktiv ab effective_start_at.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from shareomat.database.audit import record_event
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
    accepted_version: int | None = None
    declaration_issued_at: date | None = None
    acceptance_method: str = ""
    signed_at: date | None = None
    signed_by: str = ""
    signed_document_location: str = ""
    signed_document_sha256: str = ""
    recorded_by: str = ""
    recorded_at: datetime | None = None
    notice_evidence: str = ""
    vnb_notified_at: date | None = None
    vnb_confirmed_at: date | None = None
    effective_start_at: date | None = None
    effective_end_at: date | None = None
    exit_reason: str = ""
    declaration_snapshot: str = ""
    declaration_sha256: str = ""
    paper_original_location: str = ""

    @property
    def signature_evidence(self) -> str:
        """How the signature is evidenced: stored document, paper original location, or nothing."""
        if self.signed_document_sha256:
            return "document"
        if self.paper_original_location:
            return "paper"
        return ""

    @property
    def status_label(self) -> str:
        """Furthest step reached in the join status chain (see module docstring)."""
        if self.left_at is not None:
            return "ausgetreten per " + self.left_at.strftime("%d.%m.%Y")
        if self.vnb_confirmed_at is not None:
            return "vom Netzbetreiber bestätigt"
        if self.vnb_notified_at is not None:
            return "beim Netzbetreiber gemeldet"
        if self.accepted_at is not None:
            return "Unterschrift eingegangen"
        if self.declaration_issued_at is not None:
            return "Erklärung ausgestellt"
        return "angelegt"


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
        accepted_version=row["accepted_version"],
        declaration_issued_at=str_to_date(row["declaration_issued_at"]),
        acceptance_method=row["acceptance_method"],
        signed_at=str_to_date(row["signed_at"]),
        signed_by=row["signed_by"],
        signed_document_location=row["signed_document_location"],
        signed_document_sha256=row["signed_document_sha256"],
        recorded_by=row["recorded_by"],
        recorded_at=datetime.fromisoformat(row["recorded_at"]) if row["recorded_at"] else None,
        notice_evidence=row["notice_evidence"],
        vnb_notified_at=str_to_date(row["vnb_notified_at"]),
        vnb_confirmed_at=str_to_date(row["vnb_confirmed_at"]),
        effective_start_at=str_to_date(row["effective_start_at"]),
        effective_end_at=str_to_date(row["effective_end_at"]),
        exit_reason=row["exit_reason"],
        declaration_snapshot=row["declaration_snapshot"],
        declaration_sha256=row["declaration_sha256"],
        paper_original_location=row["paper_original_location"],
    )


def assign_contract(
    db_path: Path, participant_id: str, contract_version_id: int, tariff_id: int | None = None, *,
    joined_at: date | None = None, accepted_at: date | None = None,
) -> ParticipantContractAssignment:
    """Assign `participant_id` to `contract_version_id`. Refuses a second assignment of the same pair.

    No upsert: an existing row may already carry signature/notification
    evidence, which must never be overwritten by re-adding the participant.
    """
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            participant_row = conn.execute(
                "SELECT id FROM participants WHERE participant_id = ?", (participant_id,),
            ).fetchone()
            if participant_row is None:
                raise ParticipantContractError(f"Teilnehmer '{participant_id}' wurde nicht gefunden.")

            existing = conn.execute(
                "SELECT 1 FROM participant_contract WHERE participant_id = ? AND contract_version_id = ?",
                (participant_row["id"], contract_version_id),
            ).fetchone()
            if existing:
                raise ParticipantContractError(
                    f"Teilnehmer '{participant_id}' ist dieser Vertragsversion bereits zugeordnet."
                )
            version_row = conn.execute(
                "SELECT version FROM contract_versions WHERE id = ?", (contract_version_id,),
            ).fetchone()
            if version_row is None:
                raise ParticipantContractError(f"Vertragsversion {contract_version_id} wurde nicht gefunden.")
            conn.execute(
                """
                INSERT INTO participant_contract
                    (participant_id, contract_version_id, tariff_id, joined_at, left_at, accepted_at,
                     accepted_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    participant_row["id"], contract_version_id, tariff_id,
                    date_to_str(joined_at), None, date_to_str(accepted_at),
                    version_row["version"] if accepted_at else None, now,
                ),
            )
            record_event(
                conn, "participant", participant_id, "assigned",
                f"Vertragsversion v{version_row['version']}, Beitritt per {date_to_str(joined_at)}",
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


def _load_for_update(conn, assignment_id: int):
    row = conn.execute(
        """
        SELECT pc.*, p.participant_id AS participant_business_id, cv.version AS version_number
        FROM participant_contract pc
        JOIN participants p ON p.id = pc.participant_id
        JOIN contract_versions cv ON cv.id = pc.contract_version_id
        WHERE pc.id = ?
        """,
        (assignment_id,),
    ).fetchone()
    if row is None:
        raise ParticipantContractError(f"Zuordnung {assignment_id} wurde nicht gefunden.")
    return row


def _reload(db_path: Path, assignment_id: int) -> ParticipantContractAssignment:
    result = get_assignment(db_path, assignment_id)
    assert result is not None
    return result


ACCEPTANCE_METHODS = {
    "paper": "Unterschrift auf Papier",
    "scan": "unterschriebenes Dokument (Scan/PDF)",
    "qes": "qualifizierte elektronische Signatur",
}


def _freeze_declaration(conn, row, snapshot: str) -> str:
    """Store the rendered personal declaration once; returns its SHA-256 ('' if nothing to store)."""
    if row["declaration_snapshot"] or not snapshot:
        return row["declaration_sha256"]
    digest = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    conn.execute(
        "UPDATE participant_contract SET declaration_snapshot = ?, declaration_sha256 = ? WHERE id = ?",
        (snapshot, digest, row["id"]),
    )
    return digest


def mark_declaration_issued(
    db_path: Path, assignment_id: int, issued_at: date, *, snapshot: str = "",
) -> ParticipantContractAssignment:
    """Record that the Beitrittserklärung was handed out (write-once) and freeze the handed-out text."""
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            if row["declaration_issued_at"]:
                raise ParticipantContractError("Die Beitrittserklärung wurde bereits als ausgestellt erfasst.")
            conn.execute(
                "UPDATE participant_contract SET declaration_issued_at = ? WHERE id = ?",
                (date_to_str(issued_at), assignment_id),
            )
            digest = _freeze_declaration(conn, row, snapshot)
            record_event(conn, "participant", row["participant_business_id"], "declaration_issued",
                         f"v{row['version_number']} am {date_to_str(issued_at)}"
                         + (f", Erklärung-SHA-256 {digest}" if digest else ""))
    return _reload(db_path, assignment_id)


def mark_accepted(
    db_path: Path, assignment_id: int, accepted_at: date, *,
    method: str = "", signed_by: str = "", recorded_by: str = "",
    document_location: str = "", document_sha256: str = "", paper_location: str = "", snapshot: str = "",
) -> ParticipantContractAssignment:
    """Record the participant's signed Beitrittserklärung — sets accepted_at AND notified_at (write-once).

    The date is the signature date, which cannot lie in the future. The
    signature evidence (method, signer, optional stored document + hash,
    who recorded it and when) is stored with it. A second call raises —
    an existing acceptance is never overwritten. For an existing
    participant carried forward to a newer version, use mark_notified().
    """
    if accepted_at > date.today():
        raise ParticipantContractError("Das Unterschriftsdatum darf nicht in der Zukunft liegen.")
    if method and method not in ACCEPTANCE_METHODS:
        raise ParticipantContractError(f"Unbekannte Zustimmungsart '{method}'.")
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            if row["accepted_at"]:
                raise ParticipantContractError(
                    f"Der Beitritt wurde bereits am {row['accepted_at']} bestätigt und wird nicht überschrieben."
                )
            conn.execute(
                """
                UPDATE participant_contract
                SET accepted_at = ?, notified_at = ?, accepted_version = ?, signed_at = ?,
                    acceptance_method = ?, signed_by = ?, recorded_by = ?, recorded_at = ?,
                    signed_document_location = ?, signed_document_sha256 = ?, paper_original_location = ?
                WHERE id = ?
                """,
                (
                    date_to_str(accepted_at), date_to_str(accepted_at), row["version_number"],
                    date_to_str(accepted_at), method, signed_by, recorded_by, now_iso(),
                    document_location, document_sha256, paper_location, assignment_id,
                ),
            )
            declaration_digest = _freeze_declaration(conn, row, snapshot)
            detail = f"v{row['version_number']}, unterschrieben am {date_to_str(accepted_at)}"
            if method:
                detail += f", {ACCEPTANCE_METHODS[method]}"
            if signed_by:
                detail += f", durch {signed_by}"
            if document_sha256:
                detail += f", Dokument-SHA-256 {document_sha256}"
            if paper_location:
                detail += f", Papieroriginal: {paper_location}"
            if declaration_digest:
                detail += f", Erklärung-SHA-256 {declaration_digest}"
            record_event(conn, "participant", row["participant_business_id"], "accepted", detail)
    return _reload(db_path, assignment_id)


def mark_notified(
    db_path: Path, assignment_id: int, notified_at: date, *, evidence: str = "",
) -> ParticipantContractAssignment:
    """Record that a participant has been informed of the currently assigned contract version (write-once).

    Only sets notified_at (+ how it was notified) — accepted_at, the
    original join confirmation, is left untouched, matching
    LEG-Mustervertrag §8: an existing participant only needs notice of a
    contract amendment, not a new signature.
    """
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            if row["notified_at"]:
                raise ParticipantContractError("Die Mitteilung wurde bereits erfasst.")
            conn.execute(
                "UPDATE participant_contract SET notified_at = ?, notice_evidence = ? WHERE id = ?",
                (date_to_str(notified_at), evidence, assignment_id),
            )
            record_event(conn, "participant", row["participant_business_id"], "notified",
                         f"v{row['version_number']} am {date_to_str(notified_at)}" + (f" ({evidence})" if evidence else ""))
    return _reload(db_path, assignment_id)


def mark_vnb_notified(db_path: Path, assignment_id: int, notified_at: date) -> ParticipantContractAssignment:
    """Record that the join was reported to the grid operator (write-once)."""
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            if row["vnb_notified_at"]:
                raise ParticipantContractError("Die Meldung an den Netzbetreiber wurde bereits erfasst.")
            conn.execute(
                "UPDATE participant_contract SET vnb_notified_at = ? WHERE id = ?",
                (date_to_str(notified_at), assignment_id),
            )
            record_event(conn, "participant", row["participant_business_id"], "vnb_notified",
                         f"am {date_to_str(notified_at)}")
    return _reload(db_path, assignment_id)


def mark_vnb_confirmed(
    db_path: Path, assignment_id: int, confirmed_at: date, effective_start: date,
) -> ParticipantContractAssignment:
    """Record the grid operator's confirmation and the effective start it confirmed (write-once)."""
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            if row["vnb_confirmed_at"]:
                raise ParticipantContractError("Die Bestätigung des Netzbetreibers wurde bereits erfasst.")
            if not row["vnb_notified_at"]:
                raise ParticipantContractError("Zuerst die Meldung an den Netzbetreiber erfassen.")
            if effective_start.day != 1:
                raise ParticipantContractError("Der wirksame Beginn muss ein Monatserster sein.")
            conn.execute(
                "UPDATE participant_contract SET vnb_confirmed_at = ?, effective_start_at = ? WHERE id = ?",
                (date_to_str(confirmed_at), date_to_str(effective_start), assignment_id),
            )
            record_event(conn, "participant", row["participant_business_id"], "vnb_confirmed",
                         f"am {date_to_str(confirmed_at)}, wirksam ab {date_to_str(effective_start)}")
    return _reload(db_path, assignment_id)


def record_departure(
    db_path: Path, assignment_id: int, left_at: date, *, reason: str = "",
) -> ParticipantContractAssignment:
    """Record a participant's departure on their contract assignment (write-once). Deletes nothing.

    The departure date can't lie before the join, and an already recorded
    departure is never moved — a correction must be logged as such.
    """
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            if row["left_at"]:
                raise ParticipantContractError(f"Der Austritt wurde bereits per {row['left_at']} erfasst.")
            joined = str_to_date(row["joined_at"])
            if joined is not None and left_at < joined:
                raise ParticipantContractError("Der Austritt darf nicht vor dem Beitritt liegen.")
            conn.execute(
                "UPDATE participant_contract SET left_at = ?, effective_end_at = ?, exit_reason = ? WHERE id = ?",
                (date_to_str(left_at), date_to_str(left_at), reason, assignment_id),
            )
            record_event(conn, "participant", row["participant_business_id"], "departed",
                         f"per {date_to_str(left_at)}" + (f" ({reason})" if reason else ""))
    return _reload(db_path, assignment_id)


def has_any_assignment(db_path: Path) -> bool:
    """True if at least one participant has ever been assigned to any contract version.

    Used as a heuristic for "is this the community's very first-ever
    participant assignment" (LEG founding) to pick the 3-month rather than
    1-month grid-operator notice-period warning when adding a participant.
    """
    with connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM participant_contract LIMIT 1").fetchone()
        return row is not None
