# -*- coding: utf-8 -*-
"""
File: shareomat/database/participant_contract.py

Purpose:
    A participant's relation to one contract version (and the tariff that
    applied): the declaration handed out, the signature, the notice of an
    amendment and — for versions published "with consent of all" — the
    consent. Every row belongs to exactly one membership
    (shareomat.database.memberships), which holds join, departure and the
    grid operator's report/confirmation (ADR-0009).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    * A signature is stored once, on the row of the version that was
      signed (accepted_version). It also counts for every later version of
      the same membership, never for an earlier one and never across a
      re-entry. Rows from before schema 18 may still carry copies of the
      original signature; resolution prefers the original row, so the paper
      location / stored document of the original is always the one shown.
    * Reading an assignment returns the membership fields (joined_at,
      left_at, vnb_*, effective_start_at, exit_reason) and the effective
      signature, so callers see one complete picture per version.
    * notified_at is the notice of the version; consent_at is the consent
      needed for versions published "with consent of all". A notice is
      never a consent. A member who signs a version directly gets
      notified_at = accepted_at and needs no consent for that version.
    * Evidence is write-once here: a second record raises
      ParticipantContractError. Every change is appended to audit_events.
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
    """One participant's relation to one contract version, with membership data and the effective signature."""

    participant_id: str        # business key (Participant.participant_id), not the row id
    contract_version_id: int
    tariff_id: int | None
    joined_at: date | None     # ── from the membership ──
    left_at: date | None
    accepted_at: date | None   # ── effective signature (this row or an earlier version of the membership) ──
    notified_at: date | None   # ── this version ──
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
    membership_id: int | None = None
    version_number: int | None = None
    review_note: str = ""
    signature_assignment_id: int | None = None   # row that holds the effective signature
    signature_version: int | None = None         # contract version that was actually signed
    signed_paperless_id: int | None = None
    signed_paperless_asn: int | None = None
    notice_method: str = ""
    notice_paperless_id: int | None = None
    notice_paperless_asn: int | None = None
    notice_batch_id: str = ""
    notice_recorded_by: str = ""
    notice_recorded_at: datetime | None = None
    consent_at: date | None = None
    consent_evidence: str = ""
    consent_paperless_id: int | None = None
    consent_paperless_asn: int | None = None
    consent_batch_id: str = ""
    consent_recorded_by: str = ""
    consent_recorded_at: datetime | None = None
    vnb_notified_paperless_id: int | None = None
    vnb_notified_paperless_asn: int | None = None
    vnb_confirmed_paperless_id: int | None = None
    vnb_confirmed_paperless_asn: int | None = None

    @property
    def own_signature(self) -> bool:
        """True if the effective signature was given on this very version."""
        return self.accepted_at is not None and self.signature_assignment_id == self.id

    @property
    def signature_evidence(self) -> str:
        """How the signature is evidenced: stored document, Paperless, paper original location, or nothing."""
        if self.signed_document_sha256:
            return "document"
        if self.signed_paperless_id:
            return "paperless"
        if self.paper_original_location:
            return "paper"
        return ""

    @property
    def status_label(self) -> str:
        """Short summary for lists (the checklist shows every point on its own)."""
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


ACCEPTANCE_METHODS = {
    "paper": "Unterschrift auf Papier",
    "scan": "unterschriebenes Dokument (Scan/PDF)",
    "qes": "qualifizierte elektronische Signatur",
}

# ── Private helpers ─────────────────────────────────────────────────────────

_SELECT = """
    SELECT pc.*, p.participant_id AS participant_business_id, cv.version AS version_number,
           cv.consent_based AS version_consent_based,
           m.joined_at AS m_joined_at, m.left_at AS m_left_at, m.exit_reason AS m_exit_reason,
           m.vnb_notified_at AS m_vnb_notified_at, m.vnb_confirmed_at AS m_vnb_confirmed_at,
           m.effective_start_at AS m_effective_start_at, m.review_note AS m_review_note,
           m.vnb_notified_paperless_id AS m_vnb_notified_paperless_id,
           m.vnb_notified_paperless_asn AS m_vnb_notified_paperless_asn,
           m.vnb_confirmed_paperless_id AS m_vnb_confirmed_paperless_id,
           m.vnb_confirmed_paperless_asn AS m_vnb_confirmed_paperless_asn
    FROM participant_contract pc
    JOIN participants p ON p.id = pc.participant_id
    JOIN contract_versions cv ON cv.id = pc.contract_version_id
    JOIN memberships m ON m.id = pc.membership_id
"""


def _datetime(value) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _signature_version(row) -> int:
    return row["accepted_version"] if row["accepted_version"] is not None else row["version_number"]


def _signature_rows(conn, membership_ids) -> dict[int, list]:
    """Signed rows per membership (incl. withdrawn versions; _effective_signature decides)."""
    ids = sorted({m for m in membership_ids if m is not None})
    if not ids:
        return {}
    rows = conn.execute(
        f"""
        SELECT pc.*, cv.version AS version_number, cv.status AS version_status FROM participant_contract pc
        JOIN contract_versions cv ON cv.id = pc.contract_version_id
        WHERE pc.accepted_at IS NOT NULL
          AND pc.membership_id IN ({', '.join('?' * len(ids))})
        """,
        ids,
    ).fetchall()
    result: dict[int, list] = {}
    for row in rows:
        result.setdefault(row["membership_id"], []).append(row)
    return result


def _effective_signature(signed_rows: list, version_number: int, row_id: int):
    """The signature that counts for the row `row_id` of `version_number`.

    The newest one given on that version or an earlier one of the same
    membership; a signature on a withdrawn version only stays visible on
    its own row (participants sign the corrected version again). On a tie
    (old copies of one signature) the row of the signed version wins.
    """
    eligible = [r for r in signed_rows if _signature_version(r) <= version_number
                and (r["version_status"] != "withdrawn" or r["id"] == row_id)]
    if not eligible:
        return None
    return max(eligible, key=lambda r: (_signature_version(r), r["version_number"] == _signature_version(r), -r["id"]))


def _row_to_assignment(row: sqlite3.Row, signature) -> ParticipantContractAssignment:
    sig = signature
    return ParticipantContractAssignment(
        participant_id=row["participant_business_id"],
        contract_version_id=row["contract_version_id"],
        tariff_id=row["tariff_id"],
        joined_at=str_to_date(row["m_joined_at"]),
        left_at=str_to_date(row["m_left_at"]),
        accepted_at=str_to_date(sig["accepted_at"]) if sig else None,
        notified_at=str_to_date(row["notified_at"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        id=row["id"],
        accepted_version=sig["accepted_version"] if sig else None,
        declaration_issued_at=str_to_date(row["declaration_issued_at"]),
        acceptance_method=sig["acceptance_method"] if sig else "",
        signed_at=str_to_date(sig["signed_at"]) if sig else None,
        signed_by=sig["signed_by"] if sig else "",
        signed_document_location=sig["signed_document_location"] if sig else "",
        signed_document_sha256=sig["signed_document_sha256"] if sig else "",
        recorded_by=sig["recorded_by"] if sig else "",
        recorded_at=_datetime(sig["recorded_at"]) if sig else None,
        notice_evidence=row["notice_evidence"],
        vnb_notified_at=str_to_date(row["m_vnb_notified_at"]),
        vnb_confirmed_at=str_to_date(row["m_vnb_confirmed_at"]),
        effective_start_at=str_to_date(row["m_effective_start_at"]),
        effective_end_at=str_to_date(row["m_left_at"]),
        exit_reason=row["m_exit_reason"],
        declaration_snapshot=row["declaration_snapshot"],
        declaration_sha256=row["declaration_sha256"],
        paper_original_location=sig["paper_original_location"] if sig else "",
        membership_id=row["membership_id"],
        version_number=row["version_number"],
        review_note=row["m_review_note"],
        signature_assignment_id=sig["id"] if sig else None,
        signature_version=_signature_version(sig) if sig else None,
        signed_paperless_id=sig["signed_paperless_id"] if sig else None,
        signed_paperless_asn=sig["signed_paperless_asn"] if sig else None,
        notice_method=row["notice_method"],
        notice_paperless_id=row["notice_paperless_id"],
        notice_paperless_asn=row["notice_paperless_asn"],
        notice_batch_id=row["notice_batch_id"],
        notice_recorded_by=row["notice_recorded_by"],
        notice_recorded_at=_datetime(row["notice_recorded_at"]),
        consent_at=str_to_date(row["consent_at"]),
        consent_evidence=row["consent_evidence"],
        consent_paperless_id=row["consent_paperless_id"],
        consent_paperless_asn=row["consent_paperless_asn"],
        consent_batch_id=row["consent_batch_id"],
        consent_recorded_by=row["consent_recorded_by"],
        consent_recorded_at=_datetime(row["consent_recorded_at"]),
        vnb_notified_paperless_id=row["m_vnb_notified_paperless_id"],
        vnb_notified_paperless_asn=row["m_vnb_notified_paperless_asn"],
        vnb_confirmed_paperless_id=row["m_vnb_confirmed_paperless_id"],
        vnb_confirmed_paperless_asn=row["m_vnb_confirmed_paperless_asn"],
    )


def load_assignments(conn, where: str, params: tuple) -> list[ParticipantContractAssignment]:
    """Assignments matching `where` (SQL on pc/p/cv/m), with effective signatures. For this package only."""
    rows = conn.execute(f"{_SELECT} WHERE {where} ORDER BY p.participant_id, m.joined_at, pc.id", params).fetchall()
    signatures = _signature_rows(conn, [r["membership_id"] for r in rows])
    return [
        _row_to_assignment(r, _effective_signature(signatures.get(r["membership_id"], []), r["version_number"], r["id"]))
        for r in rows
    ]


def _load_for_update(conn, assignment_id: int):
    row = conn.execute(f"{_SELECT} WHERE pc.id = ?", (assignment_id,)).fetchone()
    if row is None:
        raise ParticipantContractError(f"Zuordnung {assignment_id} wurde nicht gefunden.")
    return row


def _reload(db_path: Path, assignment_id: int) -> ParticipantContractAssignment:
    result = get_assignment(db_path, assignment_id)
    assert result is not None
    return result


def _effective_signature_for(conn, row):
    signatures = _signature_rows(conn, [row["membership_id"]])
    return _effective_signature(signatures.get(row["membership_id"], []), row["version_number"], row["id"])


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


def _update_membership(conn, membership_id: int, **values) -> None:
    columns = ", ".join(f"{name} = ?" for name in values)
    conn.execute(f"UPDATE memberships SET {columns}, updated_at = ? WHERE id = ?",
                 (*values.values(), now_iso(), membership_id))


# ── Public functions ────────────────────────────────────────────────────────


def assign_contract(
    db_path: Path, participant_id: str, contract_version_id: int, tariff_id: int | None = None, *,
    joined_at: date | None = None, accepted_at: date | None = None,
) -> ParticipantContractAssignment:
    """Assign `participant_id` to `contract_version_id`, within their current membership or a new one.

    A participant without a running membership starts a new one (also a
    re-entry after a departure). One who is a member already gets the
    version added to that membership; a different join date is refused —
    it would silently change an existing membership. No upsert: an
    existing row may carry evidence that must never be overwritten.
    """
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            participant_row = conn.execute(
                "SELECT id FROM participants WHERE participant_id = ?", (participant_id,),
            ).fetchone()
            if participant_row is None:
                raise ParticipantContractError(f"Teilnehmer '{participant_id}' wurde nicht gefunden.")
            version_row = conn.execute(
                "SELECT version, valid_from FROM contract_versions WHERE id = ?", (contract_version_id,),
            ).fetchone()
            if version_row is None:
                raise ParticipantContractError(f"Vertragsversion {contract_version_id} wurde nicht gefunden.")
            starts = joined_at or str_to_date(version_row["valid_from"])
            running = conn.execute(
                "SELECT * FROM memberships WHERE participant_id = ? AND (left_at IS NULL OR left_at >= ?) "
                "ORDER BY joined_at DESC, id DESC LIMIT 1",
                (participant_row["id"], date_to_str(starts)),
            ).fetchone()
            if running is not None:
                on_version = conn.execute(
                    "SELECT 1 FROM participant_contract WHERE membership_id = ? AND contract_version_id = ?",
                    (running["id"], contract_version_id),
                ).fetchone()
                if on_version:
                    raise ParticipantContractError(
                        f"Teilnehmer '{participant_id}' ist dieser Vertragsversion bereits zugeordnet."
                    )
                if joined_at is not None and running["joined_at"] and date_to_str(joined_at) != running["joined_at"]:
                    raise ParticipantContractError(
                        f"Teilnehmer '{participant_id}' ist bereits Mitglied seit "
                        f"{str_to_date(running['joined_at']).strftime('%d.%m.%Y')} — das Beitrittsdatum dort korrigieren."
                    )
                membership_id = running["id"]
            else:
                membership_id = conn.execute(
                    "INSERT INTO memberships (participant_id, joined_at, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (participant_row["id"], date_to_str(joined_at), now, now),
                ).lastrowid
            assignment_id = conn.execute(
                """
                INSERT INTO participant_contract
                    (participant_id, membership_id, contract_version_id, tariff_id, accepted_at,
                     accepted_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    participant_row["id"], membership_id, contract_version_id, tariff_id,
                    date_to_str(accepted_at), version_row["version"] if accepted_at else None, now,
                ),
            ).lastrowid
            record_event(
                conn, "participant", participant_id, "assigned",
                f"Vertragsversion v{version_row['version']}, Beitritt per {date_to_str(joined_at)}",
            )
    return _reload(db_path, assignment_id)


def list_assignments_for_version(db_path: Path, contract_version_id: int) -> list[ParticipantContractAssignment]:
    """Return every participant assignment for one contract version (a re-entry can add a second row)."""
    with connect(db_path) as conn:
        return load_assignments(conn, "pc.contract_version_id = ?", (contract_version_id,))


def list_assignments_for_participant(db_path: Path, participant_id: str) -> list[ParticipantContractAssignment]:
    """Every assignment of one participant over all versions and memberships."""
    with connect(db_path) as conn:
        return load_assignments(conn, "p.participant_id = ?", (participant_id,))


def get_assignment(db_path: Path, assignment_id: int) -> ParticipantContractAssignment | None:
    """Return one assignment by its internal database id, or None if it does not exist."""
    with connect(db_path) as conn:
        found = load_assignments(conn, "pc.id = ?", (assignment_id,))
    return found[0] if found else None


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
    who recorded it and when) is stored with it. Refused when the
    membership already has a signature that counts for this version.
    """
    if accepted_at > date.today():
        raise ParticipantContractError("Das Unterschriftsdatum darf nicht in der Zukunft liegen.")
    if not document_sha256 and not paper_location.strip():
        raise ParticipantContractError(
            "Nachweis fehlt: unterschriebenes Dokument (Prüfsumme) oder Ablageort des Papieroriginals angeben."
        )
    if method and method not in ACCEPTANCE_METHODS:
        raise ParticipantContractError(f"Unbekannte Zustimmungsart '{method}'.")
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            existing = _effective_signature_for(conn, row)
            if existing is not None:
                raise ParticipantContractError(
                    f"Der Beitritt wurde bereits am {existing['accepted_at']} bestätigt und wird nicht überschrieben."
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

    For a version published "with consent of all" this form records the
    consent (the operator answers "Zustimmung erfassen"): it then also sets
    consent_at, which billing checks.
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
            if row["version_consent_based"]:
                conn.execute(
                    "UPDATE participant_contract SET consent_at = ?, consent_evidence = ? WHERE id = ?",
                    (date_to_str(notified_at), evidence, assignment_id),
                )
            record_event(conn, "participant", row["participant_business_id"], "notified",
                         f"v{row['version_number']} am {date_to_str(notified_at)}" + (f" ({evidence})" if evidence else ""))
    return _reload(db_path, assignment_id)


def mark_vnb_notified(db_path: Path, assignment_id: int, notified_at: date) -> ParticipantContractAssignment:
    """Record that the join was reported to the grid operator (write-once, on the membership)."""
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            if row["m_vnb_notified_at"]:
                raise ParticipantContractError("Die Meldung an den Netzbetreiber wurde bereits erfasst.")
            _update_membership(conn, row["membership_id"], vnb_notified_at=date_to_str(notified_at))
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
            if row["m_vnb_confirmed_at"]:
                raise ParticipantContractError("Die Bestätigung des Netzbetreibers wurde bereits erfasst.")
            if not row["m_vnb_notified_at"]:
                raise ParticipantContractError("Zuerst die Meldung an den Netzbetreiber erfassen.")
            if effective_start.day != 1:
                raise ParticipantContractError("Der wirksame Beginn muss ein Monatserster sein.")
            _update_membership(conn, row["membership_id"], vnb_confirmed_at=date_to_str(confirmed_at),
                               effective_start_at=date_to_str(effective_start))
            record_event(conn, "participant", row["participant_business_id"], "vnb_confirmed",
                         f"am {date_to_str(confirmed_at)}, wirksam ab {date_to_str(effective_start)}")
    return _reload(db_path, assignment_id)


def record_departure(
    db_path: Path, assignment_id: int, left_at: date, *, reason: str = "",
) -> ParticipantContractAssignment:
    """Record a participant's departure on their membership (write-once). Deletes nothing.

    Applies to every contract version of the membership. The departure
    date can't lie before the join.
    """
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            if row["m_left_at"]:
                raise ParticipantContractError(f"Der Austritt wurde bereits per {row['m_left_at']} erfasst.")
            joined = str_to_date(row["m_joined_at"])
            if joined is not None and left_at < joined:
                raise ParticipantContractError("Der Austritt darf nicht vor dem Beitritt liegen.")
            _update_membership(conn, row["membership_id"], left_at=date_to_str(left_at), exit_reason=reason)
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
