# -*- coding: utf-8 -*-
"""
File: shareomat/core/delivery/document_mail.py

Purpose:
    E-mail one delivery item of an issued document: the Rechnung or
    Gutschrift itself, its Stornobeleg, or one Zahlungserinnerung/Mahnung.
    Flow: delivery checks -> PDF of the frozen page -> filled e-mail
    template -> log "attempt" -> SMTP -> log "sent" or "failed".

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    - Only ever on the operator's request (button), never automatically.
    - The recipient is the e-mail address frozen with the document at
      issue time, the same one the document view shows.
    - Already sent, or an earlier attempt without a result: sending again
      needs resend=True (an explicit tick in the UI). Sending again only adds
      a log entry, never a second claim.
    - One send at a time in this process (lock). Inside the lock the
      document is loaded and checked again, so a double click sends once.
    - Connection broken after the hand-over (MailError.uncertain): no
      "failed" entry. The attempt stays the last entry, which the document
      view shows as "Ergebnis unklar" until someone checks the mailbox's
      "Gesendet" folder and records the outcome.
    - The page to print is injected (page_html), so this module does not
      depend on the web layer; the PDF renderer and the SMTP transport are
      injectable for tests.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from shareomat.config import SmtpConfig
from shareomat.core.delivery.mail import Attachment, MailError, OutgoingMail, SentMail, send_mail
from shareomat.core.delivery.pdf import render_pdf
from shareomat.core.email_templates import (
    cancellation_values,
    common_values,
    document_values,
    fill_template,
    reminder_values,
)
from shareomat.database.audit import log_event
from shareomat.database.community import get_community
from shareomat.database.email_templates import get_email_template
from shareomat.database.invoicing import (
    InvoicingError,
    document_contact,
    email_subject,
    record_email_attempt,
    record_email_result,
)
from shareomat.models.invoicing import BillingDocument, DocumentDelivery, DocumentReminder

logger = logging.getLogger(__name__)

PageRenderer = Callable[[BillingDocument, str, "DocumentReminder | None"], str]

_send_lock = threading.Lock()

_FILE_LABELS = {1: "Zahlungserinnerung", 2: "Mahnung-1", 3: "Mahnung-2"}

# Test e-mails with a sample document (E-Mail-Vorlagen → Test senden) are marked unmistakably.
TEST_SUBJECT_PREFIX = "[TEST] "
TEST_BODY_NOTE = ("TEST – Muster für Max Muster, keine echte Rechnung. Nicht bezahlen. So sieht die E-Mail aus, die "
                  "ein Teilnehmer bekommt.\n\n")


class DocumentMailError(RuntimeError):
    """Not sent (or outcome unknown, see uncertain); the message is for the operator (German)."""

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class MailPreview:
    """What would be sent for one item — the same values the real send uses."""

    document: BillingDocument
    reminder: DocumentReminder | None
    item: str
    title: str
    to: str
    subject: str
    body: str
    attachment_name: str
    sent_at: datetime | None
    unknown_outcome: DocumentDelivery | None


# ── Private helpers ─────────────────────────────────────────────────────────


def _template_kind(doc: BillingDocument, item: str) -> str:
    if item == "cancellation":
        return "cancellation"
    if item == "reminder":
        return "reminder"
    return "invoice" if doc.is_invoice else "credit"


def _title(doc: BillingDocument, item: str, reminder: DocumentReminder | None) -> str:
    if item == "cancellation":
        return f"Stornobeleg {doc.cancel_number}"
    if item == "reminder" and reminder is not None:
        return f"{reminder.label} zu {doc.number}"
    return f"{'Rechnung' if doc.is_invoice else 'Gutschrift'} {doc.number}"


def _attachment_name(doc: BillingDocument, item: str, reminder: DocumentReminder | None) -> str:
    if item == "cancellation":
        return f"Stornobeleg_{doc.cancel_number}.pdf"
    if item == "reminder" and reminder is not None:
        return f"{_FILE_LABELS.get(reminder.level, 'Mahnung')}_{doc.number}.pdf"
    return f"{'Rechnung' if doc.is_invoice else 'Gutschrift'}_{doc.number}.pdf"


def _sent_at(doc: BillingDocument, item: str, reminder: DocumentReminder | None) -> datetime | None:
    if item == "cancellation":
        return doc.cancellation_sent_at
    if item == "reminder" and reminder is not None:
        return reminder.sent_at
    return doc.sent_at


def _values(db_path: Path, doc: BillingDocument, item: str, reminder: DocumentReminder | None) -> dict[str, str]:
    community = get_community(db_path)
    representative, contact_email = document_contact(db_path, doc)
    common = common_values(community.name if community else "", representative, contact_email, doc.recipient_label)
    if item == "cancellation":
        return cancellation_values(common, doc)
    if item == "reminder" and reminder is not None:
        return reminder_values(common, doc, reminder)
    return document_values(common, doc)


def _preview(db_path: Path, document_id: int, item: str, reminder_id: int | None) -> MailPreview:
    try:
        doc, reminder = email_subject(db_path, document_id, item, reminder_id)
    except InvoicingError as exc:
        raise DocumentMailError(str(exc)) from None
    template = get_email_template(db_path, _template_kind(doc, item))
    subject, body = fill_template(template.subject, template.body, _values(db_path, doc, item, reminder))
    return MailPreview(
        document=doc, reminder=reminder, item=item, title=_title(doc, item, reminder), to=doc.recipient_email,
        subject=subject, body=body, attachment_name=_attachment_name(doc, item, reminder),
        sent_at=_sent_at(doc, item, reminder), unknown_outcome=doc.unknown_email_outcome(item, reminder_id),
    )


def _frozen_sha(preview: MailPreview) -> str:
    """The SHA-256 of the frozen text the PDF was made from (shown in the PDF footer too)."""
    if preview.item == "cancellation":
        return preview.document.cancel_sha256
    if preview.item == "reminder" and preview.reminder is not None:
        return preview.reminder.snapshot_sha256
    return preview.document.snapshot_sha256


# ── Public functions ────────────────────────────────────────────────────────


def preview_document_email(db_path: Path, document_id: int, item: str = "document",
                           reminder_id: int | None = None) -> MailPreview:
    """The e-mail Shareomat would send now (no PDF yet); DocumentMailError when the item may not go out."""
    return _preview(db_path, document_id, item, reminder_id)


def document_pdf(db_path: Path, document_id: int, item: str, reminder_id: int | None, page_html: PageRenderer,
                 *, pdf_renderer: Callable[[str], bytes] = render_pdf) -> tuple[str, bytes]:
    """(file name, PDF) of one item, exactly as it would be attached."""
    preview = _preview(db_path, document_id, item, reminder_id)
    return preview.attachment_name, pdf_renderer(page_html(preview.document, item, preview.reminder))


def sample_document_mail(db_path: Path, doc: BillingDocument, page_html: str, to: str,
                         *, pdf_renderer: Callable[[str], bytes] = render_pdf) -> OutgoingMail:
    """The e-mail a real document gets (same template, values and PDF), for a sample document, marked as a test.

    Nothing is logged in the delivery log: the sample is not a document.
    """
    template = get_email_template(db_path, _template_kind(doc, "document"))
    subject, body = fill_template(template.subject, template.body, _values(db_path, doc, "document", None))
    return OutgoingMail(to=to, subject=TEST_SUBJECT_PREFIX + subject, body=TEST_BODY_NOTE + body,
                        attachments=(Attachment(_attachment_name(doc, "document", None), pdf_renderer(page_html)),))


def send_document_email(db_path: Path, smtp: SmtpConfig, document_id: int, item: str = "document",
                        reminder_id: int | None = None, *, page_html: PageRenderer, resend: bool = False,
                        pdf_renderer: Callable[[str], bytes] = render_pdf,
                        transport: Callable[[SmtpConfig, OutgoingMail], SentMail] = send_mail) -> MailPreview:
    """Send one item by e-mail and log it; returns what was sent. Raises DocumentMailError otherwise."""
    with _send_lock:
        preview = _preview(db_path, document_id, item, reminder_id)
        if preview.unknown_outcome is not None and not resend:
            raise DocumentMailError(
                f"{preview.title}: Der Versand vom {preview.unknown_outcome.recorded_at:%d.%m.%Y %H:%M} hat kein "
                "Ergebnis. Bitte zuerst im Postfach unter «Gesendet» nachsehen. Ist die E-Mail dort, die Zustellung "
                "von Hand erfassen; sonst «Erneut senden» ankreuzen.")
        if preview.sent_at is not None and not resend:
            raise DocumentMailError(f"{preview.title} wurde am {preview.sent_at:%d.%m.%Y} bereits zugestellt. "
                                    "Zum nochmaligen Senden «Erneut senden» ankreuzen.")
        try:
            pdf = pdf_renderer(page_html(preview.document, item, preview.reminder))
        except Exception as exc:          # PdfError, or anything else from rendering: nothing was sent
            raise DocumentMailError(f"{preview.title}: {exc}") from None
        pdf_sha = hashlib.sha256(pdf).hexdigest()
        mail = OutgoingMail(to=preview.to, subject=preview.subject, body=preview.body,
                            attachments=(Attachment(preview.attachment_name, pdf),))
        try:
            record_email_attempt(db_path, document_id, item, reminder_id,
                                 detail=f"an {preview.to}, Anhang {preview.attachment_name}")
        except InvoicingError as exc:
            raise DocumentMailError(f"{preview.title}: {exc}") from None
        try:
            sent = transport(smtp, mail)
        except MailError as exc:
            if exc.uncertain:
                log_event(db_path, "billing_document", document_id, "delivery_uncertain", f"{preview.title}: {exc}")
            else:
                record_email_result(db_path, document_id, item, reminder_id, sent=False, detail=str(exc))
            raise DocumentMailError(f"{preview.title}: {exc}", uncertain=exc.uncertain) from None
        except Exception as exc:
            # Not a MailError: a bug, not a server answer. Whether the server got the message is unknown.
            logger.exception("Unexpected error while sending %s", preview.title)
            log_event(db_path, "billing_document", document_id, "delivery_uncertain",
                      f"{preview.title}: {type(exc).__name__}")
            raise DocumentMailError(f"{preview.title}: Unerwarteter Fehler beim Senden ({type(exc).__name__}). "
                                    "Ob die E-Mail angekommen ist, ist unklar: im Postfach unter «Gesendet» "
                                    "nachsehen.", uncertain=True) from None
        try:
            record_email_result(
                db_path, document_id, item, reminder_id, sent=True,
                detail=f"an {sent.to}, {sent.message_id}, PDF SHA-256 {pdf_sha}, "
                       f"Beleg SHA-256 {_frozen_sha(preview)}")
        except Exception:
            logger.exception("E-mail for %s was sent but could not be logged", preview.title)
            raise DocumentMailError(
                f"{preview.title} wurde gesendet, konnte aber nicht protokolliert werden. Bitte die Zustellung von "
                "Hand erfassen (Zustellweg E-Mail).", uncertain=True) from None
    return preview

