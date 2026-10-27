# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/invoices.py

Purpose:
    "Rechnungen" page: issue invoices/credit notes from released billing
    runs, record payments and producer payouts, log deliveries, and work
    through payment reminders. Business rules live in
    shareomat.database.invoicing.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Routes (relative to /invoices, dispatched via ctx.segments):
        GET  /?tab=invoices|credits|reminders|settings   overview
        GET  /<id>                       one document with its logs
        GET  /<id>/print                 frozen document, printable
        GET  /<id>/print/cancellation    frozen cancellation notice (Stornobeleg), printable
        GET  /reminders/<rid>/print      frozen reminder, printable
        GET  /runs/<run_id>/preview      all documents a run would get, with sums — approval step
        POST /issue                      approve + issue (form: run_id, confirm)
        POST /settings                   payment details (payee, IBAN)
        POST /<id>/payment               book a payment received (invoice)
        POST /<id>/refund                record an over-payment returned (invoice)
        POST /<id>/payout                record a payout made (credit)
        POST /<id>/delivery              log a delivery (target: document | cancellation | reminder:<rid>)
        POST /<id>/cancel                cancel (only while nothing was booked; Stornobeleg once delivered)
        POST /<id>/reminder              create the next due reminder
        POST /<id>/reminder-block        set/lift the Mahnsperre
        POST /reminders/<rid>/discard    discard an unsent reminder
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from shareomat.database.billing import BillingWorkflowError, list_billing_runs
from shareomat.database.invoicing import (
    InvoicingError,
    cancel_document,
    create_reminder,
    discard_reminder,
    format_iban,
    get_document,
    get_payment_details,
    get_reminder,
    issue_documents,
    issue_problems,
    list_documents,
    local_today,
    missing_documents,
    payout_status,
    preview_documents,
    record_delivery,
    record_payment,
    record_payout,
    record_refund,
    reminder_proposals,
    save_payment_details,
    set_reminder_block,
)
from shareomat.leg_const import BILLING_STATUS_RELEASED
from shareomat.models.invoicing import (
    DELIVERY_METHODS,
    DOC_TYPE_CREDIT,
    DOC_TYPE_INVOICE,
    REMINDER_OFFSETS_DAYS,
    STATE_LABELS,
)
from shareomat.web.navigation import url_for
from shareomat.web.rendering import FormError, Redirect, RequestContext, form_bool, render_page
from shareomat.web.rendering import _env as jinja_env
from shareomat.web.state import get_state

_TABS = ("invoices", "credits", "reminders", "settings")
_INTEGRITY_STAMP = "PRÜFSUMME STIMMT NICHT — der gespeicherte Beleg wurde verändert. Nicht verwenden."


def render_document(template_name: str, context: dict) -> str:
    """Renderer handed to shareomat.database.invoicing for the frozen document bodies."""
    return jinja_env.get_template(template_name).render(**context)


def _not_found(ctx: RequestContext, what: str) -> str:
    return render_page("placeholders/coming_soon.html", ctx, "invoices", title=f"{what} nicht gefunden", description="")


def _pending_runs(ctx: RequestContext) -> list[dict]:
    """Released runs that still have participants without a document."""
    pending = []
    for run in list_billing_runs(ctx.db_path):
        if run.status != BILLING_STATUS_RELEASED:
            continue
        issued, missing = missing_documents(ctx.db_path, run.id)
        if missing:
            pending.append({"run": run, "issued": issued, "missing": missing,
                            "problems": issue_problems(ctx.db_path, run.id)})
    return pending


def handle_get(ctx: RequestContext) -> str:
    """Overview with tabs, one document, or a printable document/reminder."""
    today = local_today()
    if not ctx.segments:
        tab = ctx.query.get("tab", "invoices")
        tab = tab if tab in _TABS else "invoices"
        payee, iban = get_payment_details(ctx.db_path)
        docs = list_documents(ctx.db_path, doc_type=DOC_TYPE_CREDIT if tab == "credits" else DOC_TYPE_INVOICE) \
            if tab in ("invoices", "credits") else []
        payouts = {}
        if tab == "credits":
            for run_id in {d.billing_run_id for d in docs}:
                payouts.update(payout_status(ctx.db_path, run_id))
        return render_page(
            "invoices/list.html", ctx, "invoices", tab=tab, docs=docs, today=today, state_labels=STATE_LABELS,
            pending=_pending_runs(ctx), proposals=reminder_proposals(ctx.db_path, today=today) if tab != "settings" else [],
            payouts=payouts, payee=payee, iban=format_iban(iban), offsets=REMINDER_OFFSETS_DAYS,
        )

    if len(ctx.segments) == 3 and ctx.segments[0] == "runs" and ctx.segments[2] == "preview":
        try:
            run_id = int(ctx.segments[1])
            problems = issue_problems(ctx.db_path, run_id, today=today)
        except (ValueError, BillingWorkflowError):
            return _not_found(ctx, "Abrechnung")
        planned = [] if problems else preview_documents(ctx.db_path, run_id, render_document, today=today)
        return render_page(
            "invoices/issue_preview.html", ctx, "invoices", run_id=run_id, problems=problems, planned=planned,
            total_invoices=sum((p["amount"] for p in planned if p["doc_type"] == DOC_TYPE_INVOICE), Decimal(0)),
            total_credits=sum((p["amount"] for p in planned if p["doc_type"] == DOC_TYPE_CREDIT), Decimal(0)),
        )

    if len(ctx.segments) == 3 and ctx.segments[0] == "reminders" and ctx.segments[2] == "print":
        try:
            reminder = get_reminder(ctx.db_path, int(ctx.segments[1]))
        except (ValueError, InvoicingError):
            return _not_found(ctx, "Mahnung")
        return jinja_env.get_template("invoices/print.html").render(
            title=reminder.label, body=reminder.snapshot_html, sha256=reminder.snapshot_sha256)

    try:
        doc = get_document(ctx.db_path, int(ctx.segments[0]))
    except (ValueError, InvoicingError):
        return _not_found(ctx, "Beleg")

    if ctx.segments[1:] == ["print", "cancellation"] and doc.cancel_number:
        return jinja_env.get_template("invoices/print.html").render(
            title=f"Stornobeleg {doc.cancel_number}", body=doc.cancel_snapshot_html, sha256=doc.cancel_sha256,
            stamp="" if doc.integrity_ok else _INTEGRITY_STAMP)

    if ctx.segments[1:] == ["print"]:
        stamp = f"STORNIERT — {doc.cancel_reason}" if doc.status == "cancelled" else ""
        if not doc.integrity_ok:
            stamp = _INTEGRITY_STAMP
        return jinja_env.get_template("invoices/print.html").render(
            title=f"{'Rechnung' if doc.is_invoice else 'Gutschrift'} {doc.number}", body=doc.snapshot_html,
            sha256=doc.snapshot_sha256, stamp=stamp)

    payout = None if doc.is_invoice else payout_status(ctx.db_path, doc.billing_run_id).get(doc.id)
    proposal = next((p for p in reminder_proposals(ctx.db_path, today=today) if p.document.id == doc.id), None)
    return render_page(
        "invoices/detail.html", ctx, "invoices", doc=doc, today=today, state_labels=STATE_LABELS,
        payout=payout, proposal=proposal, delivery_methods=DELIVERY_METHODS,
    )


def _form_date(ctx: RequestContext, name: str) -> date:
    raw = ctx.form.get(name, "").strip()
    if not raw:
        return local_today()
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise FormError("Ungültiges Datum.")


def _back(ctx: RequestContext, doc_id: int, message: str) -> None:
    get_state().set_flash(message, ok=True)
    raise Redirect(url_for(ctx.ingress_path, "invoices", suffix=f"/{doc_id}"))


def handle_post(ctx: RequestContext) -> str | None:
    """Dispatch the document actions; every refusal comes back as a form error."""
    try:
        return _handle_post(ctx)
    except InvoicingError as exc:
        raise FormError(str(exc))


def _handle_post(ctx: RequestContext) -> str | None:
    if ctx.segments == ["settings"]:
        save_payment_details(ctx.db_path, ctx.form.get("payee", ""), ctx.form.get("iban", ""))
        get_state().set_flash("Zahlungsangaben gespeichert.", ok=True)
        raise Redirect(url_for(ctx.ingress_path, "invoices") + "?tab=settings")

    if ctx.segments == ["issue"]:
        try:
            run_id = int(ctx.form.get("run_id", ""))
        except ValueError:
            raise FormError("Unbekannte Abrechnung.")
        if not form_bool(ctx, "confirm"):
            raise FormError("Bitte bestätigen, dass Empfänger, Beträge und Belege geprüft sind.")
        created = issue_documents(ctx.db_path, run_id, render_document)
        get_state().set_flash(f"{len(created)} Beleg(e) erstellt.", ok=True)
        raise Redirect(url_for(ctx.ingress_path, "invoices"))

    if len(ctx.segments) == 3 and ctx.segments[0] == "reminders" and ctx.segments[2] == "discard":
        try:
            reminder = get_reminder(ctx.db_path, int(ctx.segments[1]))
        except ValueError:
            raise FormError("Unbekannte Mahnung.")
        discard_reminder(ctx.db_path, reminder.id, ctx.form.get("reason", ""))
        _back(ctx, reminder.document_id, "Mahnung verworfen.")

    if len(ctx.segments) != 2:
        raise FormError("Unbekannte Aktion.")
    try:
        doc_id = int(ctx.segments[0])
    except ValueError:
        raise FormError("Unbekannter Beleg.")
    action = ctx.segments[1]
    form = ctx.form

    if action == "payment":
        record_payment(ctx.db_path, doc_id, form.get("amount", ""), _form_date(ctx, "booked_on"),
                       reference=form.get("reference", ""), note=form.get("note", ""),
                       confirm_duplicate=form_bool(ctx, "confirm_duplicate"))
        _back(ctx, doc_id, "Zahlung gebucht.")
    if action == "refund":
        record_refund(ctx.db_path, doc_id, form.get("amount", ""), _form_date(ctx, "booked_on"),
                      reference=form.get("reference", ""), note=form.get("note", ""))
        _back(ctx, doc_id, "Rückerstattung erfasst.")
    if action == "payout":
        record_payout(ctx.db_path, doc_id, form.get("amount", ""), _form_date(ctx, "booked_on"),
                      reference=form.get("reference", ""), note=form.get("note", ""),
                      confirm_duplicate=form_bool(ctx, "confirm_duplicate"))
        _back(ctx, doc_id, "Auszahlung erfasst.")
    if action == "delivery":
        target = form.get("target", "document").strip()
        item, reminder_id = target, None
        if target.startswith("reminder:") and target[9:].isdigit():
            item, reminder_id = "reminder", int(target[9:])
        record_delivery(ctx.db_path, doc_id, form.get("method", ""), status=form.get("status", "sent"),
                        detail=form.get("detail", ""), item=item, reminder_id=reminder_id)
        _back(ctx, doc_id, "Zustellung protokolliert.")
    if action == "cancel":
        doc = cancel_document(ctx.db_path, doc_id, form.get("reason", ""), render_document)
        if doc.cancel_number:
            _back(ctx, doc_id, f"Beleg storniert. Stornobeleg {doc.cancel_number} drucken bzw. senden und die "
                               "Zustellung erfassen — danach kann der Ersatzbeleg erstellt werden.")
        _back(ctx, doc_id, "Beleg storniert (war noch nicht zugestellt). Nach der Korrektur neu erstellen.")
    if action == "reminder":
        reminder = create_reminder(ctx.db_path, doc_id, render_document)
        _back(ctx, doc_id, f"{reminder.label} erstellt — bitte drucken bzw. senden und die Zustellung erfassen.")
    if action == "reminder-block":
        set_reminder_block(ctx.db_path, doc_id, form.get("reason", ""))
        _back(ctx, doc_id, "Mahnsperre gespeichert.")
    raise FormError("Unbekannte Aktion.")
