# -*- coding: utf-8 -*-
"""
File: shareomat/core/delivery/welcome_mail.py

Purpose:
    The "Willkommen" e-mail for a new participant: the welcome template,
    with the contract package (Beitrittserklärung + full Gesellschaftsvertrag)
    as one PDF. After it was sent, the Beitrittserklärung counts as handed
    out ("ausgestellt"), frozen in exactly the version that was sent.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    - Only on the operator's request, never automatically; only while the
      declaration is ready to be signed (published version, address, e-mail,
      meter) and not yet signed.
    - Log: audit events of the assignment (entity "assignment"):
      welcome_attempt right before the hand-over, then welcome_sent or
      welcome_failed. An attempt without a result means the outcome is
      unknown; the operator looks in the "Gesendet" folder and either
      records it as found or sends again.
    - Sent again only with resend=True. One send at a time (lock); the
      state is read again inside the lock, so a double click sends once.
    - The recipient is the participant's current e-mail address (nothing
      about the participant is frozen before the declaration is issued).
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from shareomat.config import SmtpConfig
from shareomat.core.delivery.mail import Attachment, MailError, OutgoingMail, SentMail, send_mail
from shareomat.core.delivery.pdf import render_pdf
from shareomat.core.email_templates import common_values, fill_template, welcome_values
from shareomat.database.audit import list_events, log_event
from shareomat.database.community import get_community
from shareomat.database.email_templates import get_email_template
from shareomat.database.participant_contract import (
    ParticipantContractAssignment,
    ParticipantContractError,
    mark_declaration_issued,
)
from shareomat.models.contract import ContractVersion
from shareomat.models.participant import Participant

logger = logging.getLogger(__name__)

_ENTITY = "assignment"
_ATTEMPT, _SENT, _FAILED, _UNCERTAIN = "welcome_attempt", "welcome_sent", "welcome_failed", "welcome_uncertain"
_send_lock = threading.Lock()


class WelcomeMailError(RuntimeError):
    """Not sent (or outcome unknown, see uncertain); the message is for the operator (German)."""

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class WelcomePackage:
    """The assignment and the package page exactly as the web layer prints it."""

    assignment: ParticipantContractAssignment
    participant: Participant
    version: ContractVersion
    declaration_text: str
    page_html: str
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class WelcomePreview:
    to: str
    subject: str
    body: str
    attachment_name: str
    sent_at: datetime | None
    unknown_since: datetime | None


# ── Private helpers ─────────────────────────────────────────────────────────


def _state(db_path: Path, assignment_id: int) -> tuple[datetime | None, datetime | None]:
    """(first sent, start of an attempt without result) from the assignment's audit events."""
    events = [e for e in list_events(db_path, entity=_ENTITY, entity_id=assignment_id, limit=500)
              if e.action in (_ATTEMPT, _SENT, _FAILED)]           # newest first
    sent = [e.occurred_at for e in events if e.action == _SENT]
    unknown = events[0].occurred_at if events and events[0].action == _ATTEMPT else None
    return (sent[-1] if sent else None), unknown


def _refusal(package: WelcomePackage) -> str:
    if package.issues:
        return "Beitrittserklärung nicht unterschriftsreif: " + " ".join(package.issues)
    if package.assignment.accepted_at is not None:
        return "Die Beitrittserklärung ist bereits unterschrieben — eine Willkommens-E-Mail ist nicht mehr nötig."
    if package.assignment.left_at is not None:
        return "Der Teilnehmer ist ausgetreten."
    if not (package.participant.email or "").strip():
        return "Für diesen Teilnehmer ist keine E-Mail-Adresse hinterlegt."
    return ""


def _values(db_path: Path, package: WelcomePackage) -> dict[str, str]:
    community = get_community(db_path)
    version = package.version
    common = common_values(community.name if community else "", version.representative_name,
                           version.representative_email, package.participant.label)
    return welcome_values(common, contract_version=version.version, contract_valid_from=version.valid_from,
                          joined_at=package.assignment.joined_at)


def _mark_issued(db_path: Path, package: WelcomePackage) -> None:
    """The declaration counts as handed out, frozen in the text that was sent (no-op if already recorded)."""
    if package.assignment.declaration_issued_at is not None:
        return
    try:
        mark_declaration_issued(db_path, package.assignment.id, date.today(), snapshot=package.declaration_text)
    except ParticipantContractError:
        pass          # recorded meanwhile (e.g. by hand): nothing to do


# ── Public functions ────────────────────────────────────────────────────────


def welcome_attachment_name(package: WelcomePackage) -> str:
    """Plain file name without personal data: the version is enough to identify the package."""
    return f"Beitrittserklaerung_und_Gesellschaftsvertrag_v{package.version.version}.pdf"


def preview_welcome(db_path: Path, package: WelcomePackage) -> WelcomePreview:
    """The welcome e-mail as it would be sent now; WelcomeMailError when it may not go out."""
    refusal = _refusal(package)
    if refusal:
        raise WelcomeMailError(refusal)
    template = get_email_template(db_path, "welcome")
    subject, body = fill_template(template.subject, template.body, _values(db_path, package))
    sent_at, unknown_since = _state(db_path, package.assignment.id)
    return WelcomePreview(to=package.participant.email.strip(), subject=subject, body=body,
                          attachment_name=welcome_attachment_name(package), sent_at=sent_at,
                          unknown_since=unknown_since)


def confirm_welcome_found(db_path: Path, package: WelcomePackage) -> None:
    """The operator found an e-mail of an unclear attempt in "Gesendet": log it as sent, mark issued."""
    _, unknown_since = _state(db_path, package.assignment.id)
    if unknown_since is None:
        raise WelcomeMailError("Es gibt keinen Versand mit unklarem Ergebnis.")
    log_event(db_path, _ENTITY, package.assignment.id, _SENT,
              "im Postfach unter «Gesendet» gefunden (manuell bestätigt)")
    _mark_issued(db_path, package)


def send_welcome(db_path: Path, smtp: SmtpConfig, package: WelcomePackage, *, resend: bool = False,
                 pdf_renderer: Callable[[str], bytes] = render_pdf,
                 transport: Callable[[SmtpConfig, OutgoingMail], SentMail] = send_mail) -> WelcomePreview:
    """Send the welcome e-mail with the contract package and log it; raises WelcomeMailError otherwise."""
    with _send_lock:
        preview = preview_welcome(db_path, package)
        if preview.unknown_since is not None and not resend:
            raise WelcomeMailError(
                f"Der Versand vom {preview.unknown_since:%d.%m.%Y %H:%M} hat kein Ergebnis. Bitte zuerst im Postfach "
                "unter «Gesendet» nachsehen: ist die E-Mail dort, «Im Postfach gefunden» wählen, sonst «Erneut "
                "senden» ankreuzen.")
        if preview.sent_at is not None and not resend:
            raise WelcomeMailError(f"Die Willkommens-E-Mail wurde am {preview.sent_at:%d.%m.%Y} bereits gesendet. "
                                   "Zum nochmaligen Senden «Erneut senden» ankreuzen.")
        try:
            pdf = pdf_renderer(package.page_html)
        except Exception as exc:          # PdfError, or anything else from rendering: nothing was sent
            raise WelcomeMailError(str(exc)) from None
        mail = OutgoingMail(to=preview.to, subject=preview.subject, body=preview.body,
                            attachments=(Attachment(preview.attachment_name, pdf),))
        assignment_id = package.assignment.id
        log_event(db_path, _ENTITY, assignment_id, _ATTEMPT, f"an {preview.to}, Anhang {preview.attachment_name}")
        try:
            sent = transport(smtp, mail)
        except MailError as exc:
            log_event(db_path, _ENTITY, assignment_id, _UNCERTAIN if exc.uncertain else _FAILED, str(exc))
            raise WelcomeMailError(str(exc), uncertain=exc.uncertain) from None
        except Exception as exc:
            logger.exception("Unexpected error while sending the welcome e-mail of assignment %s", assignment_id)
            log_event(db_path, _ENTITY, assignment_id, _UNCERTAIN, type(exc).__name__)
            raise WelcomeMailError(f"Unerwarteter Fehler beim Senden ({type(exc).__name__}). Ob die E-Mail angekommen "
                                   "ist, ist unklar: im Postfach unter «Gesendet» nachsehen.", uncertain=True) from None
        declaration_sha = hashlib.sha256(package.declaration_text.encode("utf-8")).hexdigest()
        try:
            log_event(db_path, _ENTITY, assignment_id, _SENT,
                      f"an {sent.to}, {sent.message_id}, PDF SHA-256 {hashlib.sha256(pdf).hexdigest()}, "
                      f"Erklärung SHA-256 {declaration_sha}, Vertrag SHA-256 {package.version.text_sha256}")
            _mark_issued(db_path, package)
        except Exception:
            logger.exception("Welcome e-mail of assignment %s was sent but could not be logged", assignment_id)
            raise WelcomeMailError("Die E-Mail wurde gesendet, konnte aber nicht protokolliert werden. Bitte "
                                   "«Als ausgestellt erfassen» von Hand.", uncertain=True) from None
    return preview
