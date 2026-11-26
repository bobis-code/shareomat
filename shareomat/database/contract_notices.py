# -*- coding: utf-8 -*-
"""
File: shareomat/database/contract_notices.py

Purpose:
    The contract notices Shareomat sent by e-mail or created as a letter
    ("Dokumente → Meldungen & Mitteilungen"), and recording the notice on the
    participant's contract assignment together with them.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    - E-mail: create_email_attempt() stores the row right before the
      hand-over to the mail server, finish_email() its result. Only a sent
      e-mail records the notice, in the same transaction. An attempt without
      a result (restart, lost connection) stays "Ergebnis unklar" until the
      operator resolves it (resolve_unclear) — no second e-mail before that.
    - Letter / handed over: create_letter() stores the row and records the
      notice in one transaction, or nothing at all.
    - The notice on the assignment is recorded with the same rules as the
      manual entry (participant_contract.mark_notified_in).
    - Every write takes the write lock before it reads (BEGIN IMMEDIATE).
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.database.audit import record_event
from shareomat.database.participant_contract import ParticipantContractError, mark_notified_in
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.models.contract_notice import (
    NOTICE_METHOD_EMAIL,
    NOTICE_STATUS_ATTEMPT,
    NOTICE_STATUS_CREATED,
    NOTICE_STATUS_FAILED,
    NOTICE_STATUS_SENT,
    NOTICE_STATUS_UNCERTAIN,
    SENDABLE_NOTICE_METHODS,
    ContractNotice,
)

_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
_OPEN = (NOTICE_STATUS_ATTEMPT, NOTICE_STATUS_UNCERTAIN)


class ContractNoticeError(Exception):
    """A notice that can not be stored or resolved; the message is for the operator (German)."""


# ── Private helpers ─────────────────────────────────────────────────────────


@contextmanager
def _write_txn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """One write transaction taken BEFORE the checks read anything (serializes concurrent writers)."""
    try:
        with connect(db_path) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except sqlite3.OperationalError as exc:
        if (getattr(exc, "sqlite_errorcode", 0) & 0xff) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise ContractNoticeError("Datenbank ist vorübergehend gesperrt — bitte erneut versuchen.") from exc
        raise


def _to_notice(row) -> ContractNotice:
    return ContractNotice(
        id=row["id"], assignment_id=row["assignment_id"], participant_id=row["participant_id"],
        participant_label=row["participant_label"], contract_version_id=row["contract_version_id"],
        contract_version=row["contract_version"], method=row["method"],
        notice_date=str_to_date(row["notice_date"]), recipient=row["recipient"], subject=row["subject"],
        body=row["body"], attachment_name=row["attachment_name"], contract_sha256=row["contract_sha256"],
        status=row["status"], detail=row["detail"], pdf_sha256=row["pdf_sha256"], created_by=row["created_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None,
    )


def _insert(conn, *, assignment_id: int, participant_id: str, participant_label: str, contract_version_id: int,
            contract_version: int, method: str, notice_date: date, recipient: str, subject: str, body: str,
            attachment_name: str, contract_sha256: str, status: str, created_by: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO contract_notices (assignment_id, participant_id, participant_label, contract_version_id,
            contract_version, method, notice_date, recipient, subject, body, attachment_name, contract_sha256,
            status, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (assignment_id, participant_id, participant_label, contract_version_id, contract_version, method,
         date_to_str(notice_date), recipient, subject, body, attachment_name, contract_sha256, status,
         created_by, now_iso()),
    )
    return int(cursor.lastrowid)


def _load(conn, notice_id: int):
    row = conn.execute("SELECT * FROM contract_notices WHERE id = ?", (notice_id,)).fetchone()
    if row is None:
        raise ContractNoticeError(f"Mitteilung {notice_id} wurde nicht gefunden.")
    return row


def _record_notice(conn, row, evidence: str, recorded_by: str) -> str:
    """Record the notice on the assignment; "" or why it was already recorded / is no longer possible."""
    try:
        mark_notified_in(conn, row["assignment_id"], str_to_date(row["notice_date"]), method=row["method"],
                         evidence=evidence[:500], recorded_by=recorded_by, batch_id=f"M{row['id']}")
    except ParticipantContractError as exc:
        return str(exc)
    return ""


def _finish(conn, notice_id: int, status: str, detail: str, pdf_sha256: str = "") -> None:
    """Set the result of an e-mail that has none yet (conditional update: a result is never overwritten)."""
    updated = conn.execute(
        "UPDATE contract_notices SET status = ?, detail = ?, pdf_sha256 = ?, finished_at = ? "
        "WHERE id = ? AND status IN (?, ?)",
        (status, detail[:1000], pdf_sha256, now_iso(), notice_id, *_OPEN),
    ).rowcount
    if updated != 1:
        raise ContractNoticeError("Diese Mitteilung hat bereits ein Ergebnis.")


# ── Public functions ────────────────────────────────────────────────────────


def local_today() -> date:
    """Today in Switzerland (the add-on container runs on UTC)."""
    return datetime.now(_TZ).date()


def create_email_attempt(db_path: Path, *, assignment_id: int, participant_id: str, participant_label: str,
                         contract_version_id: int, contract_version: int, recipient: str, subject: str, body: str,
                         attachment_name: str, contract_sha256: str, created_by: str) -> ContractNotice:
    """Store the e-mail right before it is handed to the mail server (status "attempt", dated today)."""
    with _write_txn(db_path) as conn:
        unclear = conn.execute(
            "SELECT id FROM contract_notices WHERE assignment_id = ? AND status IN (?, ?)",
            (assignment_id, *_OPEN)).fetchone()
        if unclear is not None:
            raise ContractNoticeError(
                f"Die E-Mail Nr. {unclear['id']} an {participant_id} hat kein Ergebnis. Bitte zuerst unter "
                "Dokumente → Meldungen & Mitteilungen klären.")
        notice_id = _insert(
            conn, assignment_id=assignment_id, participant_id=participant_id, participant_label=participant_label,
            contract_version_id=contract_version_id, contract_version=contract_version, method=NOTICE_METHOD_EMAIL,
            notice_date=local_today(), recipient=recipient, subject=subject, body=body,
            attachment_name=attachment_name, contract_sha256=contract_sha256, status=NOTICE_STATUS_ATTEMPT,
            created_by=created_by)
        record_event(conn, "participant", participant_id, "notice_email_attempt",
                     f"Vertragsmitteilung v{contract_version} Nr. {notice_id} an {recipient}")
        row = _load(conn, notice_id)
    return _to_notice(row)


def finish_email(db_path: Path, notice_id: int, *, sent: bool, uncertain: bool = False, detail: str,
                 pdf_sha256: str = "", recorded_by: str) -> str:
    """Store the result of the hand-over; a sent e-mail records the notice in the same transaction.

    Returns "" or why the notice was not recorded although the e-mail went
    out (e.g. recorded by hand meanwhile) — the e-mail stays logged as sent.
    """
    status = NOTICE_STATUS_SENT if sent else (NOTICE_STATUS_UNCERTAIN if uncertain else NOTICE_STATUS_FAILED)
    with _write_txn(db_path) as conn:
        row = _load(conn, notice_id)
        _finish(conn, notice_id, status, detail, pdf_sha256)
        note = ""
        if sent:
            note = _record_notice(conn, row, f"E-Mail an {row['recipient']}, {detail}, Mitteilung Nr. {notice_id}",
                                  recorded_by)
        record_event(conn, "participant", row["participant_id"], f"notice_email_{status}",
                     f"Vertragsmitteilung v{row['contract_version']} Nr. {notice_id}: {detail}"
                     + (f" (nicht eingetragen: {note})" if note else ""))
    return note


def create_letter(db_path: Path, *, assignment_id: int, participant_id: str, participant_label: str,
                  contract_version_id: int, contract_version: int, method: str, notice_date: date, recipient: str,
                  subject: str, body: str, attachment_name: str, contract_sha256: str,
                  created_by: str) -> ContractNotice:
    """Store a letter / handed-over notice and record the notice on the assignment — both or nothing."""
    if method not in SENDABLE_NOTICE_METHODS or method == NOTICE_METHOD_EMAIL:
        raise ContractNoticeError("Ein Schreiben gibt es nur für Brief oder persönliche Übergabe.")
    with _write_txn(db_path) as conn:
        notice_id = _insert(
            conn, assignment_id=assignment_id, participant_id=participant_id, participant_label=participant_label,
            contract_version_id=contract_version_id, contract_version=contract_version, method=method,
            notice_date=notice_date, recipient=recipient, subject=subject, body=body,
            attachment_name=attachment_name, contract_sha256=contract_sha256, status=NOTICE_STATUS_CREATED,
            created_by=created_by)
        row = _load(conn, notice_id)
        refusal = _record_notice(conn, row, f"Schreiben Nr. {notice_id} (Dokumente → Meldungen & Mitteilungen)",
                                 created_by)
        if refusal:
            raise ContractNoticeError(f"{participant_id}: {refusal}")   # rolls the row back as well
    return _to_notice(row)


def resolve_unclear(db_path: Path, notice_id: int, *, found_in_sent: bool, recorded_by: str) -> str:
    """The operator looked in "Gesendet": found = sent (records the notice), not found = not sent."""
    with _write_txn(db_path) as conn:
        row = _load(conn, notice_id)
        if row["status"] not in _OPEN:
            raise ContractNoticeError("Diese Mitteilung hat bereits ein Ergebnis.")
        note = ""
        if found_in_sent:
            _finish(conn, notice_id, NOTICE_STATUS_SENT, "im Postfach unter «Gesendet» gefunden (bestätigt)")
            note = _record_notice(conn, row, f"E-Mail an {row['recipient']}, im Postfach unter «Gesendet» gefunden, "
                                             f"Mitteilung Nr. {notice_id}", recorded_by)
        else:
            _finish(conn, notice_id, NOTICE_STATUS_FAILED, "nicht im Postfach unter «Gesendet» (bestätigt)")
        record_event(conn, "participant", row["participant_id"], "notice_email_resolved",
                     f"Vertragsmitteilung Nr. {notice_id}: " + ("gefunden" if found_in_sent else "nicht gesendet"))
    return note


def get_notice(db_path: Path, notice_id: int) -> ContractNotice | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM contract_notices WHERE id = ?", (notice_id,)).fetchone()
    return _to_notice(row) if row else None


def list_notices(db_path: Path, *, limit: int = 500) -> list[ContractNotice]:
    """Newest first."""
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM contract_notices ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_to_notice(r) for r in rows]


def unclear_notice_for(db_path: Path, assignment_id: int) -> ContractNotice | None:
    """The e-mail of this assignment whose outcome is unknown, if any."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM contract_notices WHERE assignment_id = ? AND status IN (?, ?) "
                           "ORDER BY id DESC LIMIT 1", (assignment_id, *_OPEN)).fetchone()
    return _to_notice(row) if row else None
