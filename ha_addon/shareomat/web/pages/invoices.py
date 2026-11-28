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
        POST /settings                   payment profile (account holder, address, IBAN, contact e-mail)
        POST /settings/confirm           confirm the profile after the scan test (form: version)
        POST /<id>/payment               book a payment received (invoice)
        POST /<id>/refund                record an over-payment returned (invoice)
        POST /<id>/payout                record a payout made (credit)
        POST /<id>/delivery              log a delivery (target: document | cancellation | reminder:<rid>)
        POST /<id>/cancel                cancel (only while nothing was booked; Stornobeleg once delivered)
        POST /<id>/reminder              create the next due reminder
        POST /<id>/reminder-block        set/lift the Mahnsperre
        POST /reminders/<rid>/discard    discard an unsent reminder
        GET  /dispatch?period=<run_id>   documents still to be delivered (Sammelversand)
        POST /dispatch                   confirm delivery of the ticked documents
        POST /dispatch/email             send the ticked documents by e-mail (Shareomat's own SMTP)
        GET  /runs/<run_id>/print-all    all unsent documents of a run, one A4 page each (Sammeldruck)
        GET  /<id>/email?target=…        the e-mail Shareomat would send (recipient, text, PDF), with the send button
        POST /<id>/email                 send one item by e-mail (form: target, confirm, resend)
        (GET /<id>/pdf?target=… and /sample/pdf return a PDF and are served by the web
         server itself — see pdf_download() — because they return a file, not HTML)

    target: document | cancellation | reminder:<rid>.

    Lists take ?period=<run_id>|all (default: the latest billing month) and
    ?status=… (see _STATUS_FILTERS). Recording money always needs an explicit
    "done in e-banking" confirmation: Shareomat never moves money itself.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from shareomat.config import SmtpConfig, smtp_problems
from shareomat.core.delivery.document_mail import (
    DocumentMailError,
    document_pdf,
    preview_document_email,
    send_document_email,
)
from shareomat.core.delivery.pdf import PdfError, pdf_unavailable_reason, render_pdf
from shareomat.database.billing import BillingWorkflowError, get_billing_run, list_billing_runs
from shareomat.database.invoicing import (
    InvoicingError,
    cancel_document,
    create_reminder,
    discard_reminder,
    confirm_payment_profile,
    get_document,
    get_payment_profile,
    get_reminder,
    issue_documents,
    issue_problems,
    list_documents,
    local_today,
    missing_documents,
    month_overview,
    period_label,
    unsent_documents,
    payout_status,
    documents_fingerprint,
    preview_documents,
    recipient_changes,
    record_delivery,
    record_payment,
    record_payout,
    record_refund,
    reminder_proposals,
    payment_problems,
    payment_profile_status,
    sample_document,
    sample_payment_part,
    save_payment_profile,
    set_reminder_block,
)
from shareomat.leg_const import BILLING_STATUS_RELEASED
from shareomat.core.payments.swiss_qr import format_iban, split_street
from shareomat.database.invoicing import _creditor
from shareomat.models.invoicing import (
    PROFILE_STATUS_LABELS,
    SENT_BY_LABELS,
    BillingDocument,
    DocumentReminder,
    PaymentProfile,
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
_STATUS_FILTERS = {
    "invoices": {"all": "Alle Rechnungen", "to_send": "Noch zu versenden", "open": "Offen (nicht bezahlt)",
                 "overdue": "Überfällig", "done": "Bezahlt"},
    "credits": {"all": "Alle Gutschriften", "to_send": "Noch zu versenden", "payable": "Jetzt auszahlbar",
                "open": "Noch nicht ausbezahlt", "done": "Ausbezahlt"},
}
_INTEGRITY_STAMP = "PRÜFSUMME STIMMT NICHT — der gespeicherte Beleg wurde verändert. Nicht verwenden."
_MAX_MAILS_PER_REQUEST = 100
_SAMPLE_STAMP = "MUSTER – KEINE RECHNUNG, NICHT BEZAHLEN. Test aus Shareomat für die fiktive Person Max Muster."


def render_document(template_name: str, context: dict) -> str:
    """Renderer handed to shareomat.database.invoicing for the frozen document bodies."""
    return jinja_env.get_template(template_name).render(**context)


def printable_page(doc: BillingDocument, item: str = "document", reminder: DocumentReminder | None = None,
                   *, for_pdf: bool = False) -> str:
    """The standalone page of one item (print view and PDF): frozen body, SHA-256 footer, warning stamps."""
    stamp = "" if doc.integrity_ok else _INTEGRITY_STAMP
    if item == "cancellation":
        title, body, sha256 = f"Stornobeleg {doc.cancel_number}", doc.cancel_snapshot_html, doc.cancel_sha256
    elif item == "reminder" and reminder is not None:
        title, body, sha256 = reminder.label, reminder.snapshot_html, reminder.snapshot_sha256
    else:
        title = f"{'Rechnung' if doc.is_invoice else 'Gutschrift'} {doc.number}"
        body, sha256 = doc.snapshot_html, doc.snapshot_sha256
        if doc.status == "cancelled" and not stamp:
            stamp = f"STORNIERT — {doc.cancel_reason}"
    return jinja_env.get_template("invoices/print.html").render(
        title=title, body=body, sha256=sha256, stamp=stamp, for_pdf=for_pdf)


def _pdf_page(doc: BillingDocument, item: str, reminder: DocumentReminder | None) -> str:
    return printable_page(doc, item, reminder, for_pdf=True)


def _smtp() -> SmtpConfig:
    runtime = get_state().runtime
    return runtime.smtp if runtime is not None else SmtpConfig()


def mail_problems() -> list[str]:
    """Why Shareomat can not send e-mails right now (mailbox setup, PDF support); empty = ready."""
    problems = smtp_problems(_smtp())
    reason = pdf_unavailable_reason()
    return problems + ([reason] if reason else [])


def _parse_target(raw: str) -> tuple[str, int | None]:
    raw = (raw or "document").strip()
    if raw in ("document", "cancellation"):
        return raw, None
    if raw.startswith("reminder:") and raw[9:].isdigit():
        return "reminder", int(raw[9:])
    raise FormError("Unbekanntes Zustellobjekt.")


def _target_of(item: str, reminder_id: int | None) -> str:
    return f"reminder:{reminder_id}" if item == "reminder" else item


def pdf_download(db_path, segments: list[str], query: dict) -> tuple[str, bytes]:
    """(file name, PDF) for GET /invoices/<id>/pdf?target=… or /invoices/sample/pdf (served by the web server).

    Raises DocumentMailError / PdfError / FormError with a message for the operator.
    """
    if segments == ["sample", "pdf"]:
        kind = query.get("kind", "")
        if kind in (DOC_TYPE_INVOICE, DOC_TYPE_CREDIT):
            try:
                doc = sample_document(db_path, kind, render_document)
            except InvoicingError as exc:
                raise FormError(str(exc))
            return f"{'Rechnung' if doc.is_invoice else 'Gutschrift'}_{doc.number}.pdf", render_pdf(sample_document_page(doc))
        return "Muster_PDF-Test.pdf", render_pdf(sample_pdf_page(db_path))
    if len(segments) != 2 or segments[1] != "pdf" or not segments[0].isdigit():
        raise FormError("Unbekannter Beleg.")
    item, reminder_id = _parse_target(query.get("target", "document"))
    return document_pdf(db_path, int(segments[0]), item, reminder_id, _pdf_page)


def sample_document_page(doc: BillingDocument) -> str:
    """The print page of a sample document (Max Muster) with an unmistakable MUSTER stamp."""
    return jinja_env.get_template("invoices/print.html").render(
        title=f"Muster {doc.number}", body=doc.snapshot_html, sha256=doc.snapshot_sha256, stamp=_SAMPLE_STAMP,
        for_pdf=True)


def sample_pdf_page(db_path) -> str:
    """A test page (no document, no claim) to check fonts, page size and QR payment part in the PDF."""
    body = jinja_env.get_template("invoices/pdf_sample.html").render(
        sample=sample_payment_part(db_path), today=local_today())
    return jinja_env.get_template("invoices/print.html").render(
        title="Muster – PDF-Test", body=body, sha256="– (Muster, kein Beleg)", stamp="", for_pdf=True)


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
                            "problems": issue_problems(ctx.db_path, run.id, include_payment=False)})
    return pending


def handle_get(ctx: RequestContext) -> str:
    """Overview with tabs, one document, or a printable document/reminder."""
    today = local_today()
    if not ctx.segments:
        tab = ctx.query.get("tab", "invoices")
        tab = tab if tab in _TABS else "invoices"
        profile = get_payment_profile(ctx.db_path)
        if tab == "settings" and not profile.iban:
            # Suggestions from the contract's representative; the account holder stays empty on purpose.
            rep = _creditor(ctx.db_path, today, None, None)
            street, building = split_street(rep["address_line"])
            profile = PaymentProfile(street=street, building=building, postal_code=rep["postal_code"],
                                     city=rep["city"], contact_email=rep["email"])
        docs, payouts, periods, period, status, kpis, month = [], {}, [], None, "all", [], None
        if tab in ("invoices", "credits"):
            docs, payouts, periods, period, status, kpis = _filtered_documents(ctx, tab, today)
            month = month_overview(ctx.db_path, run_id=period if isinstance(period, int) else None, today=today)
        return render_page(
            "invoices/list.html", ctx, "invoices", tab=tab, docs=docs, today=today, state_labels=STATE_LABELS,
            periods=periods, period=period, status=status, status_filters=_STATUS_FILTERS.get(tab, {}), kpis=kpis,
            month=month,
            pending=_pending_runs(ctx), proposals=reminder_proposals(ctx.db_path, today=today) if tab != "settings" else [],
            payouts=payouts, offsets=REMINDER_OFFSETS_DAYS, profile=profile, iban=format_iban(profile.iban),
            profile_status=payment_profile_status(ctx.db_path), status_labels=PROFILE_STATUS_LABELS,
            sample=sample_payment_part(ctx.db_path) if tab == "settings" else None,
        )

    if len(ctx.segments) == 3 and ctx.segments[0] == "runs" and ctx.segments[2] == "preview":
        try:
            run_id = int(ctx.segments[1])
            problems = issue_problems(ctx.db_path, run_id, today=today, include_payment=False)
        except (ValueError, BillingWorkflowError):
            return _not_found(ctx, "Abrechnung")
        planned = [] if problems else preview_documents(ctx.db_path, run_id, render_document, today=today)
        blocked = payment_problems(ctx.db_path) if any(p["doc_type"] == DOC_TYPE_INVOICE for p in planned) else []
        return render_page(
            "invoices/issue_preview.html", ctx, "invoices", run_id=run_id, problems=problems, planned=planned,
            payment_blocked=blocked, label=_run_label(ctx, run_id),
            override_reason=get_billing_run(ctx.db_path, run_id).run.release_override_reason,
            fingerprint=documents_fingerprint(planned), changes=recipient_changes(ctx.db_path, run_id),
            total_invoices=sum((p["amount"] for p in planned if p["doc_type"] == DOC_TYPE_INVOICE), Decimal(0)),
            total_credits=sum((p["amount"] for p in planned if p["doc_type"] == DOC_TYPE_CREDIT), Decimal(0)),
        )

    if ctx.segments == ["dispatch"]:
        try:
            run_id = int(ctx.query.get("period", ""))
        except ValueError:
            return _not_found(ctx, "Abrechnung")
        rows = _dispatch_rows(ctx, run_id)
        problems = mail_problems()
        mail_rows = [] if problems else _mail_rows(ctx, rows)
        return render_page(
            "invoices/dispatch.html", ctx, "invoices", run_id=run_id, rows=rows, label=_run_label(ctx, run_id),
            without_email=sorted({r["doc"].recipient_label for r in rows if not r["doc"].recipient_email}),
            mail_problems=problems, mail_rows=mail_rows)

    if len(ctx.segments) == 3 and ctx.segments[0] == "runs" and ctx.segments[2] == "print-all":
        try:
            run_id = int(ctx.segments[1])
        except ValueError:
            return _not_found(ctx, "Abrechnung")
        pages = []
        for r in _dispatch_rows(ctx, run_id):
            d = r["doc"]
            stamp = "" if d.integrity_ok else _INTEGRITY_STAMP
            if r["item"] == "cancellation":
                pages.append({"body": d.cancel_snapshot_html, "sha256": d.cancel_sha256, "stamp": stamp})
            else:
                pages.append({"body": d.snapshot_html, "sha256": d.snapshot_sha256, "stamp": stamp})
        return jinja_env.get_template("invoices/print.html").render(
            title=f"Belege {_run_label(ctx, run_id)}", pages=pages)

    if len(ctx.segments) == 3 and ctx.segments[0] == "reminders" and ctx.segments[2] == "print":
        try:
            reminder = get_reminder(ctx.db_path, int(ctx.segments[1]))
        except (ValueError, InvoicingError):
            return _not_found(ctx, "Mahnung")
        parent = get_document(ctx.db_path, reminder.document_id)
        return printable_page(parent, "reminder", reminder)

    try:
        doc = get_document(ctx.db_path, int(ctx.segments[0]))
    except (ValueError, InvoicingError):
        return _not_found(ctx, "Beleg")

    if ctx.segments[1:] == ["print", "cancellation"] and doc.cancel_number:
        return printable_page(doc, "cancellation")

    if ctx.segments[1:] == ["print"]:
        return printable_page(doc)

    if ctx.segments[1:] == ["email"]:
        return _email_preview_page(ctx, doc)

    payout = None if doc.is_invoice else payout_status(ctx.db_path, doc.billing_run_id).get(doc.id)
    proposal = next((p for p in reminder_proposals(ctx.db_path, today=today) if p.document.id == doc.id), None)
    return render_page(
        "invoices/detail.html", ctx, "invoices", doc=doc, today=today, state_labels=STATE_LABELS,
        payout=payout, proposal=proposal, delivery_methods=DELIVERY_METHODS, sent_by_labels=SENT_BY_LABELS,
        mail_items=_mail_items(doc), mail_ready=not mail_problems(),
    )


def _mail_items(doc: BillingDocument) -> list[dict]:
    """What of this document could be e-mailed now (target, label, already sent, unknown outcome)."""
    if not doc.recipient_email:
        return []
    items = []
    if doc.status == "cancelled":
        if doc.cancel_number:
            items.append({"target": "cancellation", "label": f"Stornobeleg {doc.cancel_number}",
                          "sent_at": doc.cancellation_sent_at, "unknown": doc.unknown_email_outcome("cancellation")})
        return items
    items.append({"target": "document", "label": f"{'Rechnung' if doc.is_invoice else 'Gutschrift'} {doc.number}",
                  "sent_at": doc.sent_at, "unknown": doc.unknown_email_outcome("document")})
    for r in doc.active_reminders:
        items.append({"target": f"reminder:{r.id}", "label": r.label, "sent_at": r.sent_at,
                      "unknown": doc.unknown_email_outcome("reminder", r.id)})
    return items


def _email_preview_page(ctx: RequestContext, doc: BillingDocument) -> str:
    try:
        item, reminder_id = _parse_target(ctx.query.get("target", "document"))
    except FormError:
        return _not_found(ctx, "Zustellobjekt")
    preview, refused = None, ""
    try:
        preview = preview_document_email(ctx.db_path, doc.id, item, reminder_id)
    except DocumentMailError as exc:
        refused = str(exc)
    target = _target_of(item, reminder_id)
    return render_page(
        "invoices/email_preview.html", ctx, "invoices", doc=doc, preview=preview, refused=refused,
        problems=mail_problems(), target=target,
        pdf_url=url_for(ctx.ingress_path, "invoices", suffix=f"/{doc.id}/pdf") + f"?target={target}")


def _mail_rows(ctx: RequestContext, rows: list[dict]) -> list[dict]:
    """Dispatch rows that can go by e-mail, each with the filled subject (or why not)."""
    mail_rows = []
    for row in rows:
        doc = row["doc"]
        if not doc.recipient_email:
            continue
        entry = {**row, "subject": "", "refused": "", "unknown": doc.unknown_email_outcome(row["item"]),
                 "preview_url": url_for(ctx.ingress_path, "invoices", suffix=f"/{doc.id}/email") + f"?target={row['item']}",
                 "pdf_url": url_for(ctx.ingress_path, "invoices", suffix=f"/{doc.id}/pdf") + f"?target={row['item']}"}
        try:
            entry["subject"] = preview_document_email(ctx.db_path, doc.id, row["item"]).subject
        except DocumentMailError as exc:
            entry["refused"] = str(exc)
        mail_rows.append(entry)
    return mail_rows


def _run_label(ctx: RequestContext, run_id: int) -> str:
    run = next((r for r in list_billing_runs(ctx.db_path) if r.id == run_id), None)
    return period_label(run.period_start, run.period_end) if run else ""


def _dispatch_rows(ctx: RequestContext, run_id: int) -> list[dict]:
    rows = []
    for d in sorted(unsent_documents(ctx.db_path, run_id), key=lambda d: d.number):
        if d.cancel_number and d.cancellation_sent_at is None:
            rows.append({"key": f"c{d.id}", "item": "cancellation", "doc": d, "title": f"Stornobeleg {d.cancel_number}",
                         "print_url": url_for(ctx.ingress_path, "invoices", suffix=f"/{d.id}/print/cancellation")})
        else:
            rows.append({"key": f"d{d.id}", "item": "document", "doc": d,
                         "title": f"{'Rechnung' if d.is_invoice else 'Gutschrift'} {d.number}",
                         "print_url": url_for(ctx.ingress_path, "invoices", suffix=f"/{d.id}/print")})
    return rows


def _filtered_documents(ctx: RequestContext, tab: str, today: date):
    """Documents of one billing month (default: latest) filtered by status, plus KPIs for that month."""
    doc_type = DOC_TYPE_CREDIT if tab == "credits" else DOC_TYPE_INVOICE
    all_docs = list_documents(ctx.db_path)
    run_ids = {d.billing_run_id for d in all_docs}
    runs = sorted((r for r in list_billing_runs(ctx.db_path) if r.id in run_ids),
                  key=lambda r: (r.period_end, r.version), reverse=True)
    periods = [{"id": r.id, "label": period_label(r.period_start, r.period_end)
                + (f" (Version {r.version})" if sum(1 for x in runs if x.period_end == r.period_end) > 1 else "")}
               for r in runs]
    raw = ctx.query.get("period", "")
    if raw == "all":
        period = "all"
    else:
        period = int(raw) if raw.isdigit() and int(raw) in run_ids else (runs[0].id if runs else None)
    in_period = [d for d in all_docs if period == "all" or d.billing_run_id == period]
    payouts: dict = {}
    for run_id in {d.billing_run_id for d in in_period if not d.is_invoice}:
        payouts.update(payout_status(ctx.db_path, run_id))
    status = ctx.query.get("status", "all")
    status = status if status in _STATUS_FILTERS[tab] else "all"

    def keep(d) -> bool:
        st = d.state(today)
        if status == "to_send":
            return d.status != "cancelled" and d.sent_at is None
        if status == "open":
            return d.open_chf > 0
        if status == "overdue":
            return st == "overdue"
        if status == "payable":
            return d.id in payouts and payouts[d.id].available_chf > 0
        if status == "done":
            return d.status != "cancelled" and d.open_chf == 0
        return True

    docs = [d for d in in_period if d.doc_type == doc_type and keep(d)]
    active = [d for d in in_period if d.status != "cancelled"]
    invoices = [d for d in active if d.is_invoice]
    credits = [d for d in active if not d.is_invoice]
    chf = lambda v: f"{v:.2f}"  # noqa: E731
    kpis = [
        ("Rechnungen", f"{len(invoices)}"),
        ("Versendet", f"{sum(1 for d in active if d.sent_at)}/{len(active)}"),
        ("Offen CHF", chf(sum((d.open_chf for d in invoices), Decimal(0)))),
        ("Überfällig", f"{sum(1 for d in invoices if d.state(today) == 'overdue')}"),
        ("Bezahlt CHF", chf(sum((min(d.booked_chf, d.amount_chf) for d in invoices), Decimal(0)))),
        ("Auszahlbar CHF", chf(sum((s.available_chf for s in payouts.values()), Decimal(0)))),
        ("Ausbezahlt CHF", chf(sum((d.booked_chf for d in credits), Decimal(0)))),
    ]
    return docs, payouts, periods, period, status, kpis


def _require_bank_confirmation(ctx: RequestContext, label: str) -> None:
    """Money is never moved by Shareomat: every booking needs an explicit 'done on the bank account'."""
    if not form_bool(ctx, "bank_done"):
        raise FormError(f"Bitte bestätigen: {label}")


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
        f = ctx.form
        save_payment_profile(ctx.db_path, PaymentProfile(
            payee=f.get("payee", ""), street=f.get("street", ""), building=f.get("building", ""),
            postal_code=f.get("postal_code", ""), city=f.get("city", ""), country=f.get("country", "CH"),
            iban=f.get("iban", ""), contact_email=f.get("contact_email", "")))
        status = payment_profile_status(ctx.db_path)
        get_state().set_flash("Zahlungsangaben gespeichert — Status: " + PROFILE_STATUS_LABELS[status]
                              + (". Jetzt den Musterzahlteil prüfen und bestätigen." if status != "ready" else "."),
                              ok=True)
        raise Redirect(url_for(ctx.ingress_path, "invoices") + "?tab=settings")

    if ctx.segments == ["settings", "confirm"]:
        if not (form_bool(ctx, "bank_matched") and form_bool(ctx, "scan_tested")):
            raise FormError("Bitte beide Punkte bestätigen (Bankabgleich und Scan-Test).")
        confirm_payment_profile(ctx.db_path, version=ctx.form.get("version", ""))
        pending = _pending_runs(ctx)
        if pending:
            get_state().set_flash("Bankkonto bestätigt — weiter mit der Freigabe der Belege.", ok=True)
            raise Redirect(url_for(ctx.ingress_path, "invoices", suffix=f"/runs/{pending[0]['run'].id}/preview"))
        get_state().set_flash("Bankkonto bestätigt — Rechnungen mit QR-Zahlteil sind jetzt möglich.", ok=True)
        raise Redirect(url_for(ctx.ingress_path, "invoices") + "?tab=settings")

    if ctx.segments == ["issue"]:
        try:
            run_id = int(ctx.form.get("run_id", ""))
        except ValueError:
            raise FormError("Unbekannte Abrechnung.")
        if not form_bool(ctx, "confirm"):
            raise FormError("Bitte bestätigen, dass Empfänger, Beträge und Belege geprüft sind.")
        created = issue_documents(ctx.db_path, run_id, render_document,
                                  expected_fingerprint=ctx.form.get("fingerprint", ""))
        get_state().set_flash(f"{len(created)} Beleg(e) erstellt — jetzt drucken bzw. senden.", ok=True)
        raise Redirect(url_for(ctx.ingress_path, "invoices", suffix="/dispatch") + f"?period={run_id}")

    if ctx.segments == ["dispatch", "email"]:
        _send_dispatch_emails(ctx)

    if ctx.segments == ["dispatch"]:
        try:
            run_id = int(ctx.form.get("run_id", ""))
        except ValueError:
            raise FormError("Unbekannte Abrechnung.")
        picks = {k[5:] for k in ctx.form if k.startswith("pick_")}
        if not picks:
            raise FormError("Bitte mindestens einen Beleg ankreuzen.")
        if not form_bool(ctx, "confirm"):
            raise FormError("Bitte bestätigen, dass die angekreuzten Belege tatsächlich versendet wurden.")
        rows = {r["key"]: r for r in _dispatch_rows(ctx, run_id)}
        done = 0
        for key in sorted(picks):
            row = rows.get(key)
            if row is None:
                continue
            record_delivery(ctx.db_path, row["doc"].id, ctx.form.get(f"method_{key}", "paper"),
                            detail="Sammelversand", item=row["item"])
            done += 1
        names = ", ".join(rows[k]["title"] for k in sorted(picks) if k in rows)
        get_state().set_flash(f"Zustellung von {done} Beleg(en) erfasst: {names}.", ok=True)
        raise Redirect(url_for(ctx.ingress_path, "invoices", suffix="/dispatch") + f"?period={run_id}")

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
        _require_bank_confirmation(ctx, "die Zahlung ist auf dem Bankkonto eingegangen.")
        record_payment(ctx.db_path, doc_id, form.get("amount", ""), _form_date(ctx, "booked_on"),
                       reference=form.get("reference", ""), note=form.get("note", ""),
                       confirm_duplicate=form_bool(ctx, "confirm_duplicate"))
        _back(ctx, doc_id, "Zahlung gebucht.")
    if action == "refund":
        _require_bank_confirmation(ctx, "die Rückerstattung wurde im E-Banking ausgeführt.")
        record_refund(ctx.db_path, doc_id, form.get("amount", ""), _form_date(ctx, "booked_on"),
                      reference=form.get("reference", ""), note=form.get("note", ""))
        _back(ctx, doc_id, "Rückerstattung erfasst.")
    if action == "payout":
        _require_bank_confirmation(ctx, "die Auszahlung wurde im E-Banking ausgeführt.")
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
    if action == "email":
        _send_one_email(ctx, doc_id)
    raise FormError("Unbekannte Aktion.")


def _require_mail_ready() -> None:
    problems = mail_problems()
    if problems:
        raise FormError("E-Mail-Versand nicht möglich: " + problems[0])


def _send_one_email(ctx: RequestContext, doc_id: int) -> None:
    """POST /<id>/email: send one item after the preview; always back to the document."""
    item, reminder_id = _parse_target(ctx.form.get("target", "document"))
    _require_mail_ready()
    if not form_bool(ctx, "confirm"):
        raise FormError("Bitte bestätigen, dass Empfänger, Text und PDF geprüft sind.")
    try:
        sent = send_document_email(ctx.db_path, _smtp(), doc_id, item, reminder_id, page_html=_pdf_page,
                                   resend=form_bool(ctx, "resend"))
    except DocumentMailError as exc:
        get_state().set_flash(("Ergebnis unklar — " if exc.uncertain else "Nicht gesendet — ") + str(exc), ok=False)
        raise Redirect(url_for(ctx.ingress_path, "invoices", suffix=f"/{doc_id}"))
    _back(ctx, doc_id, f"{sent.title} per E-Mail an {sent.to} gesendet.")


def _send_dispatch_emails(ctx: RequestContext) -> None:
    """POST /dispatch/email: send the ticked documents one after the other; stop at the first failure."""
    try:
        run_id = int(ctx.form.get("run_id", ""))
    except ValueError:
        raise FormError("Unbekannte Abrechnung.")
    back = url_for(ctx.ingress_path, "invoices", suffix="/dispatch") + f"?period={run_id}"
    _require_mail_ready()
    picks = sorted(k[5:] for k in ctx.form if k.startswith("mail_"))
    if not picks:
        raise FormError("Bitte mindestens einen Beleg zum Senden ankreuzen.")
    if len(picks) > _MAX_MAILS_PER_REQUEST:
        raise FormError(f"Höchstens {_MAX_MAILS_PER_REQUEST} E-Mails auf einmal.")
    if not form_bool(ctx, "confirm"):
        raise FormError("Bitte bestätigen, dass Empfänger, Text und PDFs geprüft sind.")
    rows = {r["key"]: r for r in _dispatch_rows(ctx, run_id)}
    sent, failure, not_tried = [], "", 0
    for index, key in enumerate(picks):
        row = rows.get(key)
        if row is None:
            continue                      # meanwhile delivered or no longer part of this run
        try:
            done = send_document_email(ctx.db_path, _smtp(), row["doc"].id, row["item"], page_html=_pdf_page)
        except DocumentMailError as exc:
            failure = ("Ergebnis unklar — " if exc.uncertain else "Nicht gesendet — ") + str(exc)
            not_tried = sum(1 for k in picks[index + 1:] if k in rows)
            break
        sent.append(f"{done.title} an {done.to}")
    parts = []
    if sent:
        parts.append(f"{len(sent)} E-Mail(s) gesendet: " + "; ".join(sent) + ".")
    if failure:
        parts.append(failure)
        if not_tried:
            parts.append(f"Abgebrochen: {not_tried} weitere Beleg(e) nicht versucht.")
    get_state().set_flash(" ".join(parts) or "Nichts gesendet.", ok=not failure)
    raise Redirect(back)
