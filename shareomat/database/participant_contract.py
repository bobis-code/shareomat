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
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
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
    consent_based: bool = False        # the version was published "with consent of all"

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
        consent_based=bool(row["version_consent_based"]),
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
                membership_id, membership_left = running["id"], running["left_at"]
            else:
                membership_left = None
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
            # Versions already announced for later: the new member belongs to them too
            # (as publishing would have done) — the notice for them is then open.
            later = conn.execute(
                "SELECT id, version, valid_from, tariff_id FROM contract_versions "
                "WHERE status = 'published' AND valid_from > ? ORDER BY valid_from",
                (version_row["valid_from"],),
            ).fetchall()
            carried = []
            for successor in later:
                if membership_left is not None and membership_left < successor["valid_from"]:
                    continue
                exists = conn.execute(
                    "SELECT 1 FROM participant_contract WHERE membership_id = ? AND contract_version_id = ?",
                    (membership_id, successor["id"]),
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    "INSERT INTO participant_contract (participant_id, membership_id, contract_version_id, tariff_id, "
                    "created_at) VALUES (?, ?, ?, ?, ?)",
                    (participant_row["id"], membership_id, successor["id"], successor["tariff_id"], now),
                )
                carried.append(f"v{successor['version']}")
            record_event(
                conn, "participant", participant_id, "assigned",
                f"Vertragsversion v{version_row['version']}, Beitritt per {date_to_str(joined_at)}"
                + (f"; auch angekündigte Version(en) {', '.join(carried)}" if carried else ""),
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
    paperless_id: int | None = None, paperless_asn: int | None = None,
) -> ParticipantContractAssignment:
    """Record the participant's signed Beitrittserklärung — sets accepted_at AND notified_at (write-once).

    The date is the signature date, which cannot lie in the future. At
    least one piece of evidence is required: stored document (hash),
    Paperless document number or location of the paper original. Refused
    when the membership already has a signature that counts for this version.
    """
    if accepted_at > date.today():
        raise ParticipantContractError("Das Unterschriftsdatum darf nicht in der Zukunft liegen.")
    if not document_sha256 and not paper_location.strip() and paperless_id is None:
        raise ParticipantContractError(
            "Nachweis fehlt: unterschriebenes Dokument hochladen, Paperless-Nr. oder Ablageort des Papieroriginals angeben."
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
                    signed_document_location = ?, signed_document_sha256 = ?, paper_original_location = ?,
                    signed_paperless_id = ?, signed_paperless_asn = ?
                WHERE id = ?
                """,
                (
                    date_to_str(accepted_at), date_to_str(accepted_at), row["version_number"],
                    date_to_str(accepted_at), method, signed_by, recorded_by, now_iso(),
                    document_location, document_sha256, paper_location, paperless_id, paperless_asn, assignment_id,
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
            if paperless_id is not None:
                detail += f", Paperless-Nr. {paperless_id}" + (f" (ASN {paperless_asn})" if paperless_asn else "")
            if paper_location:
                detail += f", Papieroriginal: {paper_location}"
            if declaration_digest:
                detail += f", Erklärung-SHA-256 {declaration_digest}"
            record_event(conn, "participant", row["participant_business_id"], "accepted", detail)
    return _reload(db_path, assignment_id)


NOTICE_METHODS = {
    "email": "E-Mail",
    "letter": "Brief",
    "handed": "persönlich übergeben",
    "other": "anderer Weg (siehe Nachweis)",
}


def _paperless_text(values: dict) -> str:
    if values.get("paperless_id") is None:
        return ""
    asn = values.get("paperless_asn")
    return f", Paperless-Nr. {values['paperless_id']}" + (f" (ASN {asn})" if asn else "")


def _own_signature(conn, row) -> bool:
    signature = _effective_signature_for(conn, row)
    return signature is not None and _signature_version(signature) == row["version_number"]


def _version_start(conn, row) -> date | None:
    found = conn.execute("SELECT valid_from FROM contract_versions WHERE id = ?", (row["contract_version_id"],)).fetchone()
    return str_to_date(found["valid_from"]) if found else None


def _step_notice(conn, row, values: dict, *, write: bool, recorded_by: str, batch_id: str) -> None:
    if row["notified_at"]:
        raise ParticipantContractError("Mitteilung bereits erfasst")
    if _own_signature(conn, row):
        raise ParticipantContractError("hat diese Version selbst unterschrieben")
    left, starts = str_to_date(row["m_left_at"]), _version_start(conn, row)
    if left is not None and starts is not None and left < starts:
        raise ParticipantContractError("vor Beginn dieser Version ausgetreten")
    method = values.get("method", "")
    if method and method not in NOTICE_METHODS:
        raise ParticipantContractError(f"Unbekannte Versandart '{method}'.")
    if not write:
        return
    conn.execute(
        """
        UPDATE participant_contract SET notified_at = ?, notice_method = ?, notice_evidence = ?,
            notice_paperless_id = ?, notice_paperless_asn = ?, notice_batch_id = ?,
            notice_recorded_by = ?, notice_recorded_at = ? WHERE id = ?
        """,
        (date_to_str(values["day"]), method, values.get("evidence", ""), values.get("paperless_id"),
         values.get("paperless_asn"), batch_id, recorded_by, now_iso(), row["id"]),
    )
    detail = f"v{row['version_number']} am {date_to_str(values['day'])}"
    if method:
        detail += f", {NOTICE_METHODS[method]}"
    if values.get("evidence"):
        detail += f" ({values['evidence']})"
    detail += _paperless_text(values) + (f", Sammel-Nr. {batch_id}" if batch_id else "")
    record_event(conn, "participant", row["participant_business_id"], "notified", detail)


def _step_consent(conn, row, values: dict, *, write: bool, recorded_by: str, batch_id: str) -> None:
    if not row["version_consent_based"]:
        raise ParticipantContractError("Version braucht keine Zustimmung")
    if row["consent_at"]:
        raise ParticipantContractError("Zustimmung bereits erfasst")
    if _own_signature(conn, row):
        raise ParticipantContractError("hat diese Version selbst unterschrieben")
    if values["day"] > date.today():
        raise ParticipantContractError("Das Datum der Zustimmung darf nicht in der Zukunft liegen.")
    if not values.get("evidence") and values.get("paperless_id") is None:
        raise ParticipantContractError("Nachweis der Zustimmung fehlt.")
    if not write:
        return
    conn.execute(
        """
        UPDATE participant_contract SET consent_at = ?, consent_evidence = ?, consent_paperless_id = ?,
            consent_paperless_asn = ?, consent_batch_id = ?, consent_recorded_by = ?, consent_recorded_at = ?
        WHERE id = ?
        """,
        (date_to_str(values["day"]), values.get("evidence", ""), values.get("paperless_id"),
         values.get("paperless_asn"), batch_id, recorded_by, now_iso(), row["id"]),
    )
    record_event(conn, "participant", row["participant_business_id"], "consented",
                 f"v{row['version_number']} am {date_to_str(values['day'])}"
                 + (f" ({values['evidence']})" if values.get("evidence") else "") + _paperless_text(values)
                 + (f", Sammel-Nr. {batch_id}" if batch_id else ""))


def _step_vnb_notified(conn, row, values: dict, *, write: bool, recorded_by: str, batch_id: str) -> None:
    if row["m_vnb_notified_at"]:
        raise ParticipantContractError("Meldung an den Netzbetreiber bereits erfasst")
    if not write:
        return
    _update_membership(conn, row["membership_id"], vnb_notified_at=date_to_str(values["day"]),
                       vnb_notified_paperless_id=values.get("paperless_id"),
                       vnb_notified_paperless_asn=values.get("paperless_asn"))
    record_event(conn, "participant", row["participant_business_id"], "vnb_notified",
                 f"am {date_to_str(values['day'])}" + _paperless_text(values)
                 + (f", Sammel-Nr. {batch_id}" if batch_id else ""))


def _step_vnb_confirmed(conn, row, values: dict, *, write: bool, recorded_by: str, batch_id: str) -> None:
    if row["m_vnb_confirmed_at"]:
        raise ParticipantContractError("Bestätigung des Netzbetreibers bereits erfasst")
    start = values["effective_start"]
    if values["day"] > date.today():
        raise ParticipantContractError("Das Bestätigungsdatum darf nicht in der Zukunft liegen.")
    if start.day != 1:
        raise ParticipantContractError("Der wirksame Beginn muss ein Monatserster sein.")
    joined = str_to_date(row["m_joined_at"])
    if joined is not None and start < joined:
        raise ParticipantContractError(f"wirksamer Beginn liegt vor dem Beitritt ({joined.strftime('%d.%m.%Y')})")
    if not write:
        return
    _update_membership(conn, row["membership_id"], vnb_confirmed_at=date_to_str(values["day"]),
                       effective_start_at=date_to_str(start),
                       vnb_confirmed_paperless_id=values.get("paperless_id"),
                       vnb_confirmed_paperless_asn=values.get("paperless_asn"))
    record_event(conn, "participant", row["participant_business_id"], "vnb_confirmed",
                 f"am {date_to_str(values['day'])}, wirksam ab {date_to_str(start)}" + _paperless_text(values)
                 + (f", Sammel-Nr. {batch_id}" if batch_id else ""))


# One rule set for the single forms and the collective actions.
BATCH_KINDS = {
    "notice": {"step": _step_notice, "title": "Vertragsmitteilung", "date_label": "Versanddatum",
               "evidence_placeholder": "z. B. E-Mail mit Vertrag als PDF",
               "hint": "Für Teilnehmer, die diese Version nicht selbst unterschrieben haben. Eine Mitteilung ist "
                       "keine Zustimmung."},
    "consent": {"step": _step_consent, "title": "Zustimmung zur Vertragsversion",
                "date_label": "Zustimmung erhalten am",
                "evidence_placeholder": "z. B. von allen unterschriebenes Protokoll vom …",
                "hint": "Nur wenn ein gemeinsamer Nachweis die ausdrückliche Zustimmung jedes ausgewählten "
                        "Teilnehmers belegt. Sonst die Zustimmung einzeln erfassen."},
    "vnb_notified": {"step": _step_vnb_notified, "title": "Bei EBL gemeldet", "date_label": "Gemeldet am",
                     "evidence_placeholder": "z. B. Meldung im EBL-Portal",
                     "hint": "Dieselbe Meldung an den Netzbetreiber für mehrere Teilnehmer."},
    "vnb_confirmed": {"step": _step_vnb_confirmed, "title": "Von EBL bestätigt", "date_label": "Bestätigt am",
                      "evidence_placeholder": "z. B. Bestätigungsschreiben vom …",
                      "hint": "Ein Bestätigungsschreiben mit demselben Beginn für mehrere Teilnehmer."},
}


def _single(db_path: Path, kind: str, assignment_id: int, values: dict,
            recorded_by: str) -> ParticipantContractAssignment:
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            BATCH_KINDS[kind]["step"](conn, row, values, write=True, recorded_by=recorded_by, batch_id="")
    return _reload(db_path, assignment_id)


def mark_notified(
    db_path: Path, assignment_id: int, notified_at: date, *, evidence: str = "", method: str = "",
    paperless_id: int | None = None, paperless_asn: int | None = None, recorded_by: str = "",
) -> ParticipantContractAssignment:
    """Record that a participant was informed of this contract version (write-once). Never a consent."""
    return _single(db_path, "notice", assignment_id,
                   {"day": notified_at, "evidence": evidence, "method": method,
                    "paperless_id": paperless_id, "paperless_asn": paperless_asn}, recorded_by)


def mark_consent(
    db_path: Path, assignment_id: int, consent_at: date, *, evidence: str = "",
    paperless_id: int | None = None, paperless_asn: int | None = None, recorded_by: str = "",
) -> ParticipantContractAssignment:
    """Record the participant's consent to a version published "with consent of all" (write-once, with evidence)."""
    return _single(db_path, "consent", assignment_id,
                   {"day": consent_at, "evidence": evidence, "paperless_id": paperless_id,
                    "paperless_asn": paperless_asn}, recorded_by)


def mark_vnb_notified(
    db_path: Path, assignment_id: int, notified_at: date, *,
    paperless_id: int | None = None, paperless_asn: int | None = None,
) -> ParticipantContractAssignment:
    """Record that the join was reported to the grid operator (write-once, on the membership)."""
    return _single(db_path, "vnb_notified", assignment_id,
                   {"day": notified_at, "paperless_id": paperless_id, "paperless_asn": paperless_asn}, "")


def mark_vnb_confirmed(
    db_path: Path, assignment_id: int, confirmed_at: date, effective_start: date, *,
    paperless_id: int | None = None, paperless_asn: int | None = None,
) -> ParticipantContractAssignment:
    """Record the grid operator's confirmation and the effective start it confirmed (write-once).

    No order: it can be recorded without a recorded report (the report may
    have happened outside Shareomat). The start must be a first of month
    and not before the join; the confirmation date not in the future.
    """
    return _single(db_path, "vnb_confirmed", assignment_id,
                   {"day": confirmed_at, "effective_start": effective_start, "paperless_id": paperless_id,
                    "paperless_asn": paperless_asn}, "")


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


@dataclass
class BatchCheck:
    ok: list            # [(assignment_id, participant id)]
    skipped: list       # [(participant id, reason)]


@dataclass
class BatchResult:
    batch_id: str
    done: list          # participant ids
    skipped: list       # [(participant id, reason)]


def _batch(db_path: Path, kind: str, assignment_ids: list[int], values: dict, *, write: bool,
           recorded_by: str) -> tuple[list, list, str]:
    if kind not in BATCH_KINDS:
        raise ParticipantContractError("Unbekannte Sammelaktion.")
    step = BATCH_KINDS[kind]["step"]
    batch_id = secrets.token_hex(6) if write else ""
    ok, skipped = [], []
    with connect(db_path) as conn:
        with conn:          # one transaction: an unexpected error stores nothing at all
            for assignment_id in sorted(set(assignment_ids)):
                try:
                    row = _load_for_update(conn, assignment_id)
                except ParticipantContractError:
                    skipped.append((f"Zuordnung {assignment_id}", "nicht gefunden"))
                    continue
                try:
                    step(conn, row, values, write=write, recorded_by=recorded_by, batch_id=batch_id)
                except ParticipantContractError as exc:
                    skipped.append((row["participant_business_id"], str(exc)))
                    continue
                ok.append((assignment_id, row["participant_business_id"]))
            if write and ok:
                record_event(conn, "batch", batch_id, kind,
                             f"{BATCH_KINDS[kind]['title']}: {len(ok)} Teilnehmer ({', '.join(p for _, p in ok)})"
                             + (f", übersprungen: {len(skipped)}" if skipped else ""))
    return ok, skipped, batch_id


def preview_batch(db_path: Path, kind: str, assignment_ids: list[int], **values) -> BatchCheck:
    """What a collective action would do — checks every selected row with the same rules, writes nothing."""
    ok, skipped, _ = _batch(db_path, kind, assignment_ids, values, write=False, recorded_by="")
    return BatchCheck(ok=ok, skipped=skipped)


def apply_batch(db_path: Path, kind: str, assignment_ids: list[int], *, recorded_by: str, **values) -> BatchResult:
    """Apply a previewed collective action in one transaction; rows that no longer qualify are skipped and listed.

    Each stored row and the batch itself get an audit entry; the batch id
    is stored on every notice/consent row it created.
    """
    ok, skipped, batch_id = _batch(db_path, kind, assignment_ids, values, write=True, recorded_by=recorded_by)
    return BatchResult(batch_id=batch_id, done=[p for _, p in ok], skipped=skipped)


# ── Corrections ─────────────────────────────────────────────────────────────
#
# Every recorded value can be corrected or cleared. The audit log keeps the
# old and the new value, who did it and why. A change that can alter billing
# needs a reason as soon as a released billing run covers the affected days
# (the run itself is never changed — ADR-0001; the caller shows the
# "Korrekturabrechnung prüfen" notice for runs whose result now differs).

_UNSET = object()

# section -> {field: (table, label, billing relevant)}
CORRECTION_FIELDS = {
    "membership": {
        "joined_at": ("m", "Beitritt", True),
        "left_at": ("m", "Austritt", True),
        "exit_reason": ("m", "Austrittsgrund", False),
        "vnb_notified_at": ("m", "beim Netzbetreiber gemeldet", False),
        "vnb_notified_paperless_id": ("m", "Paperless-Nr. Meldung", False),
        "vnb_notified_paperless_asn": ("m", "ASN Meldung", False),
        "vnb_confirmed_at": ("m", "vom Netzbetreiber bestätigt", True),
        "effective_start_at": ("m", "wirksamer Beginn", True),
        "vnb_confirmed_paperless_id": ("m", "Paperless-Nr. Bestätigung", False),
        "vnb_confirmed_paperless_asn": ("m", "ASN Bestätigung", False),
    },
    "signature": {
        "signed_at": ("sig", "Unterschriftsdatum", False),
        "acceptance_method": ("sig", "Art der Zustimmung", False),
        "signed_by": ("sig", "unterzeichnet von", False),
        "paper_original_location": ("sig", "Ablageort Papieroriginal", False),
        "signed_paperless_id": ("sig", "Paperless-Nr. Unterschrift", False),
        "signed_paperless_asn": ("sig", "ASN Unterschrift", False),
    },
    "declaration": {
        "declaration_issued_at": ("pc", "Erklärung ausgestellt", False),
    },
    "notice": {
        "notified_at": ("pc", "Mitteilung", False),
        "notice_method": ("pc", "Versandart", False),
        "notice_evidence": ("pc", "Nachweis Mitteilung", False),
        "notice_paperless_id": ("pc", "Paperless-Nr. Mitteilung", False),
        "notice_paperless_asn": ("pc", "ASN Mitteilung", False),
    },
    "consent": {
        "consent_at": ("pc", "Zustimmung", True),
        "consent_evidence": ("pc", "Nachweis Zustimmung", False),
        "consent_paperless_id": ("pc", "Paperless-Nr. Zustimmung", False),
        "consent_paperless_asn": ("pc", "ASN Zustimmung", False),
    },
}


@dataclass
class CorrectionResult:
    assignment: ParticipantContractAssignment
    changes: list            # [(label, old, new)]
    affected_run_ids: list   # released billing runs covering the affected days


def _shown(value) -> str:
    if value is None or value == "":
        return "leer"
    if isinstance(value, date):
        return value.strftime("%d.%m.%Y")
    text = str(value)
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            return date.fromisoformat(text).strftime("%d.%m.%Y")
        except ValueError:
            return text
    return text


def _stored(value):
    return date_to_str(value) if isinstance(value, date) else value


def _validate_membership(conn, row, merged: dict) -> None:
    joined, left = str_to_date(merged["joined_at"]), str_to_date(merged["left_at"])
    start = str_to_date(merged["effective_start_at"])
    confirmed = str_to_date(merged["vnb_confirmed_at"])
    if joined is None:
        raise ParticipantContractError("Das Beitrittsdatum kann nicht entfernt werden.")
    if joined.day != 1:
        raise ParticipantContractError("Der Beitritt muss ein Monatserster sein.")
    if left is not None:
        if (left + timedelta(days=1)).day != 1:
            raise ParticipantContractError("Der Austritt muss ein Monatsletzter sein.")
        if left < joined:
            raise ParticipantContractError("Der Austritt darf nicht vor dem Beitritt liegen.")
    if start is not None:
        if start.day != 1:
            raise ParticipantContractError("Der wirksame Beginn muss ein Monatserster sein.")
        if start < joined:
            raise ParticipantContractError("Der wirksame Beginn darf nicht vor dem Beitritt liegen.")
    if confirmed is not None and confirmed > date.today():
        raise ParticipantContractError("Das Bestätigungsdatum darf nicht in der Zukunft liegen.")
    others = conn.execute(
        "SELECT joined_at, left_at FROM memberships WHERE participant_id = ? AND id != ?",
        (row["participant_id"], row["membership_id"]),
    ).fetchall()
    for other in others:
        o_joined, o_left = str_to_date(other["joined_at"]), str_to_date(other["left_at"])
        starts_before_end = o_left is None or joined <= o_left
        ends_after_start = left is None or o_joined is None or left >= o_joined
        if starts_before_end and ends_after_start:
            raise ParticipantContractError("Die Mitgliedschaft würde sich mit einer anderen Mitgliedschaft "
                                           "desselben Teilnehmers überschneiden.")


def _affected_from(section: str, field: str, old, new, row, version_start: date | None) -> date | None:
    """First day whose billing can change through this correction."""
    if section == "membership" and field in ("joined_at", "left_at", "effective_start_at"):
        candidates = [str_to_date(v) if isinstance(v, str) else v for v in (old, new)]
        candidates = [c for c in candidates if c is not None]
        if field == "left_at" and candidates:
            return min(candidates) + timedelta(days=1) if None in (old, new) else min(candidates)
        if field == "effective_start_at":
            candidates.append(str_to_date(row["m_joined_at"]))
        return min(c for c in candidates if c is not None) if candidates else None
    if section == "membership" and field == "vnb_confirmed_at":
        return str_to_date(row["m_joined_at"])
    if section == "consent":
        return version_start
    if section == "signature_removal":
        return str_to_date(row["m_joined_at"])
    return None


def _signature_copies(conn, signature) -> list[int]:
    """The row holding a signature plus old copies of it (before schema 18) in the same membership."""
    version = _signature_version(signature)
    rows = conn.execute(
        """
        SELECT pc.id, pc.accepted_version, cv.version AS version_number FROM participant_contract pc
        JOIN contract_versions cv ON cv.id = pc.contract_version_id
        WHERE pc.membership_id = ? AND pc.accepted_at IS NOT NULL
        """,
        (signature["membership_id"],),
    ).fetchall()
    return [r["id"] for r in rows if _signature_version(r) == version]


def _released_runs_from(conn, from_day: date | None) -> list[int]:
    if from_day is None:
        return []
    rows = conn.execute(
        "SELECT r.id FROM billing_runs r JOIN billing_periods bp ON bp.id = r.billing_period_id "
        "WHERE r.status = 'released' AND bp.period_end >= ? ORDER BY r.id",
        (date_to_str(from_day),),
    ).fetchall()
    return [r["id"] for r in rows]


def correct_assignment(
    db_path: Path, assignment_id: int, section: str, values: dict, *, reason: str = "", recorded_by: str = "",
) -> CorrectionResult:
    """Correct (or clear with None) recorded values of one section; logged as old -> new.

    `values` holds only the fields to change. Membership fields change the
    membership (all its versions); signature fields change the row that
    holds the signature. A billing-relevant change covered by a released
    billing run is refused without a reason.
    """
    if section not in CORRECTION_FIELDS:
        raise ParticipantContractError("Unbekannter Bereich für eine Korrektur.")
    fields = CORRECTION_FIELDS[section]
    unknown = set(values) - set(fields)
    if unknown:
        raise ParticipantContractError("Unbekannte Felder: " + ", ".join(sorted(unknown)))
    reason = (reason or "").strip()
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            version_start = _version_start(conn, row)
            if section == "membership":
                current = {f: row["m_" + f] for f in fields}
                target_table, target_id = "memberships", row["membership_id"]
            elif section == "signature":
                signature = _effective_signature_for(conn, row)
                if signature is None:
                    raise ParticipantContractError("Es ist keine Unterschrift erfasst.")
                current = {f: signature[f] for f in fields}
                target_table, target_id = "participant_contract", signature["id"]
                target_ids = _signature_copies(conn, signature)
            else:
                current = {f: row[f] for f in fields}
                target_table, target_id = "participant_contract", row["id"]
                if section == "consent" and not row["version_consent_based"]:
                    raise ParticipantContractError("Diese Version braucht keine Zustimmung.")

            new_values = {f: _stored(v) for f, v in values.items()}
            changes = [(f, current[f], new_values[f]) for f in new_values if (current[f] or None) != (new_values[f] or None)]
            if not changes:
                raise ParticipantContractError("Keine Änderung erkannt.")
            merged = {**current, **new_values}
            if section == "membership":
                _validate_membership(conn, row, {**{f: row["m_" + f] for f in CORRECTION_FIELDS["membership"]},
                                                 **new_values})
            if section == "signature":
                signed = str_to_date(merged["signed_at"])
                if signed is None:
                    raise ParticipantContractError("Zum Entfernen der Unterschrift «Unterschrift entfernen» verwenden.")
                if signed > date.today():
                    raise ParticipantContractError("Das Unterschriftsdatum darf nicht in der Zukunft liegen.")
                if merged["acceptance_method"] and merged["acceptance_method"] not in ACCEPTANCE_METHODS:
                    raise ParticipantContractError("Unbekannte Zustimmungsart.")
                sig_row = conn.execute("SELECT signed_document_sha256 FROM participant_contract WHERE id = ?",
                                       (target_id,)).fetchone()
                if not sig_row["signed_document_sha256"] and not (merged["paper_original_location"] or "").strip() \
                        and merged["signed_paperless_id"] is None:
                    raise ParticipantContractError("Mindestens ein Nachweis zur Unterschrift muss bleiben.")
            if section == "notice" and merged["notice_method"] and merged["notice_method"] not in NOTICE_METHODS:
                raise ParticipantContractError("Unbekannte Versandart.")
            if section == "consent" and merged["consent_at"] and str_to_date(merged["consent_at"]) > date.today():
                raise ParticipantContractError("Das Datum der Zustimmung darf nicht in der Zukunft liegen.")

            affected: set[int] = set()
            for field_name, old, new in changes:
                if fields[field_name][2]:
                    affected |= set(_released_runs_from(
                        conn, _affected_from(section, field_name, old, new, row, version_start)))
            if affected and not reason:
                raise ParticipantContractError(
                    "Diese Änderung betrifft eine bereits freigegebene Abrechnung (Lauf "
                    + ", ".join(str(r) for r in sorted(affected)) + "). Bitte eine Begründung angeben."
                )
            assignments = ", ".join(f"{f} = ?" for f, _, _ in changes)
            params = [new for _, _, new in changes]
            if section == "signature" and "signed_at" in new_values:
                assignments += ", accepted_at = ?"
                params.append(new_values["signed_at"])
            if target_table == "memberships":
                assignments += ", updated_at = ?"
                params.append(now_iso())
            ids = target_ids if section == "signature" else [target_id]
            conn.execute(f"UPDATE {target_table} SET {assignments} WHERE id IN ({', '.join('?' * len(ids))})",
                         (*params, *ids))
            labelled = [(fields[f][1], _shown(old), _shown(new)) for f, old, new in changes]
            detail = f"Korrektur v{row['version_number']}: " + "; ".join(f"{label}: {old} → {new}"
                                                                          for label, old, new in labelled)
            if reason:
                detail += f"; Grund: {reason}"
            if affected:
                detail += "; betrifft freigegebene Abrechnung(en) " + ", ".join(str(r) for r in sorted(affected))
            record_event(conn, "participant", row["participant_business_id"], "corrected", detail)
    return CorrectionResult(assignment=_reload(db_path, assignment_id), changes=labelled,
                            affected_run_ids=sorted(affected))


def remove_signature(db_path: Path, assignment_id: int, *, reason: str = "") -> CorrectionResult:
    """Clear a wrongly recorded signature (all its fields go to the audit log). Billing-relevant."""
    reason = (reason or "").strip()
    with connect(db_path) as conn:
        with conn:
            row = _load_for_update(conn, assignment_id)
            signature = _effective_signature_for(conn, row)
            if signature is None:
                raise ParticipantContractError("Es ist keine Unterschrift erfasst.")
            affected = _released_runs_from(conn, _affected_from("signature_removal", "", None, None, row, None))
            if affected and not reason:
                raise ParticipantContractError(
                    "Das Entfernen betrifft eine bereits freigegebene Abrechnung (Lauf "
                    + ", ".join(str(r) for r in affected) + "). Bitte eine Begründung angeben."
                )
            old = {k: signature[k] for k in ("accepted_at", "accepted_version", "acceptance_method", "signed_by",
                                             "signed_document_sha256", "paper_original_location",
                                             "signed_paperless_id")}
            ids = _signature_copies(conn, signature)
            conn.execute(
                f"""
                UPDATE participant_contract SET accepted_at = NULL, accepted_version = NULL, signed_at = NULL,
                    acceptance_method = '', signed_by = '', signed_document_location = '',
                    signed_document_sha256 = '', paper_original_location = '', signed_paperless_id = NULL,
                    signed_paperless_asn = NULL,
                    notified_at = CASE WHEN id = ? AND notified_at = ? THEN NULL ELSE notified_at END
                WHERE id IN ({', '.join('?' * len(ids))})
                """,
                (signature["id"], signature["accepted_at"], *ids),
            )
            detail = "Unterschrift entfernt (vorher: " + ", ".join(f"{k}={_shown(v)}" for k, v in old.items()) + ")"
            if reason:
                detail += f"; Grund: {reason}"
            if affected:
                detail += "; betrifft freigegebene Abrechnung(en) " + ", ".join(str(r) for r in affected)
            record_event(conn, "participant", row["participant_business_id"], "signature_removed", detail)
    return CorrectionResult(assignment=_reload(db_path, assignment_id),
                            changes=[("Unterschrift", _shown(old["accepted_at"]), "leer")], affected_run_ids=affected)


def has_any_assignment(db_path: Path) -> bool:
    """True if at least one participant has ever been assigned to any contract version.

    Used as a heuristic for "is this the community's very first-ever
    participant assignment" (LEG founding) to pick the 3-month rather than
    1-month grid-operator notice-period warning when adding a participant.
    """
    with connect(db_path) as conn:
        row = conn.execute("SELECT 1 FROM participant_contract LIMIT 1").fetchone()
        return row is not None
