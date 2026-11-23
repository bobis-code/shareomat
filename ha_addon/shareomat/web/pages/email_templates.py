# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/email_templates.py

Purpose:
    "E-Mail-Vorlagen" admin page: edit the texts of the e-mails Shareomat
    sends (Willkommen, Rechnung, Gutschrift, Stornobeleg, Mahnung), see
    which placeholders a text may use, and preview it with example values.
    The overview also shows whether the outgoing mailbox (add-on options,
    section smtp) is ready and sends a test e-mail.

    GET  /email-templates               overview of all templates + mailbox status
    GET  /email-templates/<kind>        edit one template, with preview
    POST /email-templates/<kind>        save
    POST /email-templates/<kind>/reset  restore the default text
    POST /email-templates/test          send a test e-mail (form: to_address, what = invoice | credit |
                                        connection): a sample document for Max Muster, sent exactly like a
                                        real one but marked TEST, or the plain connection test

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import date
from decimal import Decimal

from shareomat.config import SmtpConfig, smtp_problems
from shareomat.core.delivery.document_mail import sample_document_mail
from shareomat.core.delivery.mail import Attachment, MailError, OutgoingMail, send_mail, valid_address
from shareomat.core.delivery.pdf import PdfError, pdf_unavailable_reason, render_pdf
from shareomat.core.email_templates import (
    MAX_BODY_CHARS,
    MAX_SUBJECT_CHARS,
    TEMPLATE_SPECS,
    TemplateError,
    cancellation_values,
    common_values,
    document_values,
    fill_template,
    reminder_values,
    welcome_values,
)
from shareomat.database.audit import log_event
from shareomat.database.community import get_community
from shareomat.database.contract_settings import get_contract_settings
from shareomat.database.email_templates import get_email_template, list_email_templates, reset_email_template, \
    save_email_template
from shareomat.database.invoicing import InvoicingError, get_payment_profile, sample_document
from shareomat.models.invoicing import DOC_TYPE_CREDIT, DOC_TYPE_INVOICE, BillingDocument, DocumentReminder
from shareomat.security.context import current_principal
from shareomat.web.navigation import url_for
from shareomat.web.rendering import FormError, RequestContext, render_page
from shareomat.web.state import get_state

# Synthetic example values for the preview — never real participant data.
_EXAMPLE_PARTICIPANT = "Erika Muster"
_EXAMPLE_PERIOD = (date(2026, 11, 1), date(2026, 11, 30))

# A text refused on save is shown again once (per user and kind) instead of being lost.
# In memory only, at most one per user and kind, never stored.
_rejected_drafts: dict[tuple[int, str], tuple[str, str]] = {}
_MAX_REJECTED_DRAFTS = 20

# Test e-mails ("Test senden"): what can be sent, and at most this many per hour.
TEST_KINDS = {
    DOC_TYPE_INVOICE: "Muster-Rechnung (Max Muster, wie echt)",
    DOC_TYPE_CREDIT: "Muster-Gutschrift (Max Muster, wie echt)",
    "connection": "Verbindungstest (Muster-Zahlteil)",
}
_TEST_MAILS_PER_HOUR = 20
_test_sent: deque[float] = deque()
_test_lock = threading.Lock()


# ── Private helpers ─────────────────────────────────────────────────────────


def _example_document(kind: str) -> BillingDocument:
    is_invoice = kind != "credit"
    return BillingDocument(
        billing_run_id=0, participant_id="beispiel", number="R-2026-0001" if is_invoice else "G-2026-0001",
        doc_type=DOC_TYPE_INVOICE if is_invoice else DOC_TYPE_CREDIT,
        amount_chf=Decimal("23.45"), issue_date=date(2026, 12, 5), due_date=date(2027, 1, 4),
        recipient_label=_EXAMPLE_PARTICIPANT, period_start=_EXAMPLE_PERIOD[0], period_end=_EXAMPLE_PERIOD[1],
        local_kwh=Decimal("213.2"), cancel_number="S-2026-0001" if kind == "cancellation" else "",
    )


def _example_reminder() -> DocumentReminder:
    return DocumentReminder(document_id=0, level=1, issue_date=date(2027, 1, 9), open_amount_chf=Decimal("23.45"),
                            new_due_date=date(2027, 1, 19))


def _example_values(ctx: RequestContext, kind: str) -> dict[str, str]:
    """Own community and representative (if entered), everything else synthetic."""
    community = get_community(ctx.db_path)
    rep = get_contract_settings(ctx.db_path)
    common = common_values(
        community.name if community and community.name else "LEG Musterdorf",
        rep.representative_name or "Max Vertreter",
        rep.representative_email or "leg@example.ch",
        _EXAMPLE_PARTICIPANT,
    )
    if kind == "welcome":
        return welcome_values(common, contract_version=5, contract_valid_from=date(2027, 1, 1),
                              joined_at=date(2027, 1, 1))
    if kind == "cancellation":
        return cancellation_values(common, _example_document(kind))
    if kind == "reminder":
        return reminder_values(common, _example_document(kind), _example_reminder())
    return document_values(common, _example_document(kind))


def _smtp() -> SmtpConfig | None:
    runtime = get_state().runtime
    return runtime.smtp if runtime is not None else None


def _smtp_problems(smtp: SmtpConfig | None) -> list[str]:
    return smtp_problems(smtp) if smtp is not None else ["Keine Laufzeitkonfiguration geladen."]


def _suggested_addresses(ctx: RequestContext, smtp: SmtpConfig | None) -> list[str]:
    """Shareomat's own addresses, offered as suggestions for the test recipient (any valid address is allowed)."""
    suggestions = []
    if smtp is not None and smtp.from_address:
        suggestions.append(smtp.from_address)
    contact = get_payment_profile(ctx.db_path).contact_email or get_contract_settings(ctx.db_path).representative_email
    if contact and contact not in suggestions:
        suggestions.append(contact)
    return suggestions


def _take_test_slot() -> bool:
    """At most _TEST_MAILS_PER_HOUR test e-mails per hour (in memory): the form must not become a mass mailer."""
    now = time.monotonic()
    with _test_lock:
        while _test_sent and now - _test_sent[0] > 3600:
            _test_sent.popleft()
        if len(_test_sent) >= _TEST_MAILS_PER_HOUR:
            return False
        _test_sent.append(now)
        return True


def _test_mail(ctx: RequestContext, what: str, to: str) -> OutgoingMail:
    """The test e-mail: a sample document exactly like a real one, or the plain connection test."""
    from shareomat.web.pages.invoices import render_document, sample_document_page, sample_pdf_page

    if what in (DOC_TYPE_INVOICE, DOC_TYPE_CREDIT):
        try:
            doc = sample_document(ctx.db_path, what, render_document)
        except InvoicingError as exc:
            raise FormError(f"Muster nicht erstellt: {exc}")
        return sample_document_mail(ctx.db_path, doc, sample_document_page(doc), to)
    body = ("Diese Test-E-Mail hat Shareomat gesendet. Der Versand über dieses Postfach funktioniert.\n\n"
            "Im Anhang: Muster-PDF (keine Rechnung) zum Prüfen von Schrift und QR-Zahlteil.\n")
    return OutgoingMail(to=to, subject="Shareomat: Test-E-Mail", body=body,
                        attachments=(Attachment("Muster_PDF-Test.pdf", render_pdf(sample_pdf_page(ctx.db_path))),))


def _mailbox_view(ctx: RequestContext) -> dict:
    """Status of the outgoing mailbox for the overview (never the password)."""
    smtp = _smtp()
    sender = ""
    if smtp is not None:
        sender = f"{smtp.from_name} <{smtp.from_address}>" if smtp.from_name else smtp.from_address
    return {
        "problems": _smtp_problems(smtp),
        "pdf_problem": pdf_unavailable_reason(),
        "sender": sender,
        "server": f"{smtp.host}:{smtp.port} ({smtp.security})" if smtp is not None else "",
        "suggestions": _suggested_addresses(ctx, smtp),
        "test_kinds": TEST_KINDS,
        "limit": _TEST_MAILS_PER_HOUR,
    }


def _send_test_mail(ctx: RequestContext) -> str:
    """POST /email-templates/test: one test e-mail (form: to_address, what) — sent like the real one, marked TEST."""
    back = url_for(ctx.ingress_path, "email_templates")
    smtp = _smtp()
    problems = _smtp_problems(smtp)
    if problems:
        raise FormError("Test nicht möglich: " + problems[0])
    to = ctx.form.get("to_address", "").strip()
    if not valid_address(to):
        raise FormError("Bitte eine gültige E-Mail-Adresse eintragen (z. B. name@example.ch).")
    what = ctx.form.get("what", "")
    if what not in TEST_KINDS:
        raise FormError("Bitte wählen, was gesendet werden soll.")
    try:
        mail = _test_mail(ctx, what, to)
    except PdfError as exc:
        raise FormError(f"Test nicht gesendet, PDF fehlt: {exc}")
    if not _take_test_slot():
        raise FormError(f"Höchstens {_TEST_MAILS_PER_HOUR} Test-E-Mails pro Stunde. Bitte später wieder.")
    label = TEST_KINDS[what]
    try:
        send_mail(smtp, mail)
    except MailError as exc:
        log_event(ctx.db_path, "email", "test", "failed", f"{label} an {to}: {exc}")
        get_state().set_flash(f"Test-E-Mail nicht gesendet: {exc}", ok=False)
        return back
    log_event(ctx.db_path, "email", "test", "sent", f"{label} an {to}")
    get_state().set_flash(f"{label} an {to} gesendet. Bitte im Posteingang prüfen und das PDF öffnen.", ok=True)
    return back


def _kind(ctx: RequestContext) -> str | None:
    kind = ctx.segments[0] if ctx.segments else ""
    return kind if kind in TEMPLATE_SPECS else None


def _draft_key(kind: str) -> tuple[int, str] | None:
    principal = current_principal()
    return (principal.user_id, kind) if principal is not None else None


def _park_rejected(kind: str, subject: str, body: str) -> None:
    key = _draft_key(kind)
    if key is None:
        return
    if len(_rejected_drafts) >= _MAX_REJECTED_DRAFTS:
        _rejected_drafts.clear()
    _rejected_drafts[key] = (subject[:MAX_SUBJECT_CHARS * 2], body[:MAX_BODY_CHARS * 2])


def _pop_rejected(kind: str) -> tuple[str, str] | None:
    key = _draft_key(kind)
    return _rejected_drafts.pop(key, None) if key is not None else None


def _edit_url(ctx: RequestContext, kind: str) -> str:
    return url_for(ctx.ingress_path, "email_templates", suffix=f"/{kind}")


# ── Public interface ────────────────────────────────────────────────────────


def handle_get(ctx: RequestContext) -> str:
    """Overview, or one template with its placeholders and preview."""
    kind = _kind(ctx)
    if kind is None:
        templates = {t.kind: t for t in list_email_templates(ctx.db_path)}
        return render_page("email_templates/list.html", ctx, "email_templates",
                           specs=list(TEMPLATE_SPECS.values()), templates=templates, mailbox=_mailbox_view(ctx))
    template = get_email_template(ctx.db_path, kind)
    preview_subject, preview_body = fill_template(template.subject, template.body, _example_values(ctx, kind))
    rejected = _pop_rejected(kind)
    return render_page(
        "email_templates/edit.html", ctx, "email_templates",
        spec=TEMPLATE_SPECS[kind], template=template, rejected=rejected,
        preview_subject=preview_subject, preview_body=preview_body,
        max_subject=MAX_SUBJECT_CHARS, max_body=MAX_BODY_CHARS,
    )


def handle_post(ctx: RequestContext) -> str | None:
    """Save or reset one template (back to its edit page), or send a test e-mail (back to the overview)."""
    if ctx.segments == ["test"]:
        return _send_test_mail(ctx)
    kind = _kind(ctx)
    if kind is None:
        raise FormError("Unbekannte Vorlage.")
    action = ctx.segments[1] if len(ctx.segments) > 1 else ""
    label = TEMPLATE_SPECS[kind].label
    if action == "reset":
        _pop_rejected(kind)
        reset_email_template(ctx.db_path, kind)
        get_state().set_flash(f"Vorlage «{label}»: Standardtext wiederhergestellt.", ok=True)
        return _edit_url(ctx, kind)
    if action:
        raise FormError("Unbekannte Aktion.")
    subject, body = ctx.form.get("subject", ""), ctx.form.get("body", "")
    try:
        save_email_template(ctx.db_path, kind, subject, body)
    except TemplateError as exc:
        # The stored text is unchanged; the edit page shows the refused text again.
        _park_rejected(kind, subject, body)
        get_state().set_flash(f"Nicht gespeichert: {exc}", ok=False)
        return _edit_url(ctx, kind)
    get_state().set_flash(f"Vorlage «{label}» gespeichert.", ok=True)
    return _edit_url(ctx, kind)
