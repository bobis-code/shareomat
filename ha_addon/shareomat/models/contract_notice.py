# -*- coding: utf-8 -*-
"""
File: shareomat/models/contract_notice.py

Purpose:
    One contract notice Shareomat produced for a participant: the e-mail it
    sent (with the contract as PDF attachment) or the letter it created for
    printing, kept under "Dokumente → Meldungen & Mitteilungen".

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container — no business logic, no I/O. The contract text
    itself is not copied: it is the published, unchangeable text of the
    version (contract_versions.contract_text_snapshot, checked by
    contract_sha256), so the attachment can be produced again any time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

NOTICE_METHOD_EMAIL = "email"
NOTICE_METHOD_LETTER = "letter"
NOTICE_METHOD_HANDED = "handed"
# Versandarten offered when Shareomat produces the notice itself.
SENDABLE_NOTICE_METHODS = {
    NOTICE_METHOD_EMAIL: "E-Mail",
    NOTICE_METHOD_LETTER: "Brief",
    NOTICE_METHOD_HANDED: "persönlich übergeben",
}

NOTICE_STATUS_ATTEMPT = "attempt"       # e-mail about to be handed over; no result yet
NOTICE_STATUS_SENT = "sent"             # e-mail accepted by the mail server
NOTICE_STATUS_FAILED = "failed"         # e-mail not sent
NOTICE_STATUS_UNCERTAIN = "uncertain"   # connection lost during the hand-over: outcome unknown
NOTICE_STATUS_CREATED = "created"       # letter / handed over: document created
NOTICE_STATUS_LABELS = {
    NOTICE_STATUS_ATTEMPT: "Ergebnis unklar",
    NOTICE_STATUS_SENT: "gesendet",
    NOTICE_STATUS_FAILED: "nicht gesendet",
    NOTICE_STATUS_UNCERTAIN: "Ergebnis unklar",
    NOTICE_STATUS_CREATED: "erstellt",
}


@dataclass(frozen=True)
class ContractNotice:
    """A notice e-mail or letter for one participant and one contract version."""

    id: int
    assignment_id: int
    participant_id: str
    participant_label: str
    contract_version_id: int
    contract_version: int
    method: str
    notice_date: date
    recipient: str
    subject: str
    body: str
    attachment_name: str
    contract_sha256: str
    status: str
    detail: str
    pdf_sha256: str
    created_by: str
    created_at: datetime
    finished_at: datetime | None

    @property
    def outcome_unknown(self) -> bool:
        """An e-mail that may or may not have left: the operator checks the "Gesendet" folder."""
        return self.status in (NOTICE_STATUS_ATTEMPT, NOTICE_STATUS_UNCERTAIN)

    @property
    def status_label(self) -> str:
        return NOTICE_STATUS_LABELS.get(self.status, self.status)

    @property
    def method_label(self) -> str:
        return SENDABLE_NOTICE_METHODS.get(self.method, self.method)
