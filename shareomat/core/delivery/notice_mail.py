# -*- coding: utf-8 -*-
"""
File: shareomat/core/delivery/notice_mail.py

Purpose:
    Contract notice ("Vertragsmitteilung") to participants who did not sign
    a new contract version themselves: by e-mail with the contract as PDF
    attachment, or as a letter (notice text + contract) for printing. Every
    notice is kept under "Dokumente → Meldungen & Mitteilungen" and recorded on
    the participant's contract assignment.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    - The text is the "contract_notice" e-mail template; the letter shows
      the same text above the contract.
    - Who may get a notice follows the same rules as the manual entry
      (participant_contract.preview_batch): not already recorded, not
      signed by the participant, not left before the version starts.
    - E-mail: only on the operator's request; one send at a time (lock),
      the state is read again inside the lock. Stored right before the
      hand-over, the result right after; only a sent e-mail counts as
      notice. An attempt without a result blocks a second e-mail until the
      operator checked the "Gesendet" folder.
    - An e-mail always goes out today; a letter carries the operator's date.
    - Recipient: the participant's current e-mail / address (as for the
      welcome e-mail); the document keeps what was used.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from shareomat.config import SmtpConfig
from shareomat.core.delivery.mail import Attachment, MailError, OutgoingMail, SentMail, send_mail
from shareomat.core.delivery.pdf import render_pdf
from shareomat.core.email_templates import common_values, contract_notice_values, fill_template
from shareomat.database.community import get_community
from shareomat.database.contract_notices import (
    ContractNoticeError,
    create_email_attempt,
    create_letter,
    finish_email,
    unclear_notice_for,
)
from shareomat.database.contract_versions import get_contract_version
from shareomat.database.email_templates import get_email_template
from shareomat.database.participant_contract import ParticipantContractError, get_assignment, preview_batch
from shareomat.database.participants import get_participant
from shareomat.models.contract import ContractVersion
from shareomat.models.contract_notice import NOTICE_METHOD_EMAIL, SENDABLE_NOTICE_METHODS, ContractNotice

logger = logging.getLogger(__name__)

_send_lock = threading.Lock()
TEST_SUBJECT_PREFIX = "[TEST] "
TEST_BODY_NOTE = ("TEST – So sieht die Vertragsmitteilung an den Teilnehmer aus. Sie wurde nicht an ihn gesendet "
                  "und nicht als Mitteilung eingetragen.\n\n")


class NoticeMailError(RuntimeError):
    """Not sent / not created (or outcome unknown, see uncertain); the message is for the operator (German)."""

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class NoticeDraft:
    """The notice for one participant as it would go out now; refusal says why it can not."""

    assignment_id: int | None
    participant_id: str
    participant_label: str
    method: str
    notice_date: date
    to: str              # e-mail address, or the postal address (lines) of a letter
    subject: str
    body: str
    attachment_name: str
    refusal: str = ""

    @property
    def method_label(self) -> str:
        return SENDABLE_NOTICE_METHODS.get(self.method, self.method)


@dataclass(frozen=True)
class NoticeOutcome:
    """Result of sending / creating the notices of one selection."""

    done: list[ContractNotice]
    failed: list[tuple[str, str]]          # (participant id, reason)
    stopped: bool                          # stopped at the first failed e-mail
    notes: list[tuple[str, str]]           # sent, but the notice was not recorded (participant id, why)


# ── Private helpers ─────────────────────────────────────────────────────────


def _address_lines(participant) -> str:
    lines = [participant.label, participant.address_line,
             f"{participant.postal_code or ''} {participant.city or ''}".strip()]
    return "\n".join(line.strip() for line in lines if line and line.strip())


def _text(db_path: Path, version: ContractVersion, participant_label: str) -> tuple[str, str]:
    community = get_community(db_path)
    common = common_values(community.name if community else "", version.representative_name,
                           version.representative_email, participant_label)
    template = get_email_template(db_path, "contract_notice")
    return fill_template(template.subject, template.body,
                         contract_notice_values(common, contract_version=version.version,
                                                contract_valid_from=version.valid_from))


def _draft(db_path: Path, assignment_id: int, version: ContractVersion, method: str, notice_date: date) -> NoticeDraft:
    assignment = get_assignment(db_path, assignment_id)
    participant = get_participant(db_path, assignment.participant_id) if assignment else None
    if assignment is None or participant is None:
        return NoticeDraft(assignment_id, f"Zuordnung {assignment_id}", "", method, notice_date, "", "", "", "",
                           refusal="nicht gefunden")
    subject, body = _text(db_path, version, participant.label)
    refusal = ""
    if method == NOTICE_METHOD_EMAIL:
        to = (participant.email or "").strip()
        if not to:
            refusal = "keine E-Mail-Adresse hinterlegt"
        elif (unclear := unclear_notice_for(db_path, assignment_id)) is not None:
            refusal = (f"die E-Mail vom {unclear.created_at:%d.%m.%Y %H:%M} hat kein Ergebnis — zuerst unter "
                       "Dokumente → Meldungen & Mitteilungen klären")
    else:
        to = _address_lines(participant)
        if not ((participant.address_line or "").strip() and (participant.city or "").strip()):
            refusal = "Adresse unvollständig"
    return NoticeDraft(assignment_id, participant.participant_id, participant.label, method, notice_date, to,
                       subject, body, notice_attachment_name(version), refusal=refusal)


def _send_one(db_path: Path, smtp: SmtpConfig, draft: NoticeDraft, version: ContractVersion, pdf: bytes, *,
              recorded_by: str, transport: Callable[[SmtpConfig, OutgoingMail], SentMail]) -> tuple[ContractNotice, str]:
    try:
        notice = create_email_attempt(
            db_path, assignment_id=draft.assignment_id, participant_id=draft.participant_id,
            participant_label=draft.participant_label, contract_version_id=version.id,
            contract_version=version.version, recipient=draft.to, subject=draft.subject, body=draft.body,
            attachment_name=draft.attachment_name, contract_sha256=version.text_sha256, created_by=recorded_by)
    except ContractNoticeError as exc:
        raise NoticeMailError(str(exc)) from None
    mail = OutgoingMail(to=draft.to, subject=draft.subject, body=draft.body,
                        attachments=(Attachment(draft.attachment_name, pdf),))
    try:
        sent = transport(smtp, mail)
    except MailError as exc:
        finish_email(db_path, notice.id, sent=False, uncertain=exc.uncertain, detail=str(exc), recorded_by=recorded_by)
        raise NoticeMailError(str(exc), uncertain=exc.uncertain) from None
    except Exception as exc:
        logger.exception("Unexpected error while sending contract notice %s", notice.id)
        finish_email(db_path, notice.id, sent=False, uncertain=True, detail=type(exc).__name__,
                     recorded_by=recorded_by)
        raise NoticeMailError(f"Unerwarteter Fehler beim Senden ({type(exc).__name__}). Ob die E-Mail angekommen ist, "
                              "ist unklar: im Postfach unter «Gesendet» nachsehen.", uncertain=True) from None
    pdf_sha = hashlib.sha256(pdf).hexdigest()
    try:
        note = finish_email(db_path, notice.id, sent=True, detail=f"{sent.message_id}, PDF SHA-256 {pdf_sha}",
                            pdf_sha256=pdf_sha, recorded_by=recorded_by)
    except Exception:
        logger.exception("Contract notice %s was sent but could not be logged", notice.id)
        raise NoticeMailError("Die E-Mail wurde gesendet, konnte aber nicht protokolliert werden. Bitte unter "
                              "Dokumente → Meldungen & Mitteilungen «Im Postfach gefunden» wählen.", uncertain=True) from None
    return notice, note


# ── Public functions ────────────────────────────────────────────────────────


def notice_attachment_name(version: ContractVersion) -> str:
    """Plain file name without personal data."""
    return f"Gesellschaftsvertrag_v{version.version}.pdf"


def build_drafts(db_path: Path, version_id: int, assignment_ids: list[int], *, method: str,
                 notice_date: date) -> list[NoticeDraft]:
    """The notice per selected participant, or why it can not be sent/created (same rules as the manual entry)."""
    if method not in SENDABLE_NOTICE_METHODS:
        raise NoticeMailError("Bitte die Versandart wählen.")
    version = get_contract_version(db_path, version_id)
    if version is None or version.status != "published" or not version.text_sha256:
        raise NoticeMailError("Diese Vertragsversion ist nicht veröffentlicht.")
    try:
        checked = preview_batch(db_path, "notice", assignment_ids, day=notice_date, method=method, evidence="")
    except ParticipantContractError as exc:
        raise NoticeMailError(str(exc)) from None
    drafts = [_draft(db_path, assignment_id, version, method, notice_date) for assignment_id, _ in checked.ok]
    drafts += [NoticeDraft(None, pid, pid, method, notice_date, "", "", "", "", refusal=why)
               for pid, why in checked.skipped]
    return drafts


def test_mail(draft: NoticeDraft, pdf: bytes, to: str) -> OutgoingMail:
    """The e-mail of `draft` exactly as the participant would get it, marked TEST, to another address."""
    return OutgoingMail(to=to, subject=TEST_SUBJECT_PREFIX + draft.subject, body=TEST_BODY_NOTE + draft.body,
                        attachments=(Attachment(draft.attachment_name, pdf),))


def send_notices(db_path: Path, smtp: SmtpConfig | None, version_id: int, assignment_ids: list[int], *,
                 method: str, notice_date: date, recorded_by: str, contract_page_html: str,
                 pdf_renderer: Callable[[str], bytes] = render_pdf,
                 transport: Callable[[SmtpConfig, OutgoingMail], SentMail] = send_mail) -> NoticeOutcome:
    """Send (e-mail) or create (letter, handed over) the notices of the selection.

    E-mails stop at the first failure (a wrong password or a broken
    connection would hit every following one too).
    """
    version = get_contract_version(db_path, version_id)
    if version is None:
        raise NoticeMailError("Vertragsversion nicht gefunden.")
    done, failed, notes = [], [], []
    with _send_lock:
        drafts = build_drafts(db_path, version_id, assignment_ids, method=method, notice_date=notice_date)
        for draft in drafts:
            if draft.refusal:
                failed.append((draft.participant_id, draft.refusal))
        ready = [d for d in drafts if not d.refusal]
        if method != NOTICE_METHOD_EMAIL:
            for draft in ready:
                try:
                    done.append(create_letter(
                        db_path, assignment_id=draft.assignment_id, participant_id=draft.participant_id,
                        participant_label=draft.participant_label, contract_version_id=version.id,
                        contract_version=version.version, method=method, notice_date=notice_date,
                        recipient=draft.to, subject=draft.subject, body=draft.body,
                        attachment_name=draft.attachment_name, contract_sha256=version.text_sha256,
                        created_by=recorded_by))
                except ContractNoticeError as exc:
                    failed.append((draft.participant_id, str(exc)))
            return NoticeOutcome(done, failed, False, notes)
        if not ready:
            return NoticeOutcome(done, failed, False, notes)
        if smtp is None:
            raise NoticeMailError("E-Mail-Versand ist nicht eingerichtet.")
        try:
            pdf = pdf_renderer(contract_page_html)
        except Exception as exc:          # PdfError or anything else from rendering: nothing was sent
            raise NoticeMailError(f"PDF des Vertrags nicht erstellt: {exc}") from None
        for index, draft in enumerate(ready):
            try:
                notice, note = _send_one(db_path, smtp, draft, version, pdf, recorded_by=recorded_by,
                                         transport=transport)
            except NoticeMailError as exc:
                failed.append((draft.participant_id, ("Ergebnis unklar: " if exc.uncertain else "") + str(exc)))
                stopped = index + 1 < len(ready)
                failed += [(d.participant_id, "nicht gesendet (Versand nach dem Fehler angehalten)")
                           for d in ready[index + 1:]]
                return NoticeOutcome(done, failed, stopped, notes)
            done.append(notice)
            if note:
                notes.append((draft.participant_id, note))
    return NoticeOutcome(done, failed, False, notes)
