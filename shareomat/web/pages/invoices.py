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
        GET  /runs/<run_id>/print-all    all unsent documents of a run, one A4 page each (Sammeldruck)

    Lists take ?period=<run_id>|all (default: the latest billing month) and
    ?status=… (see _STATUS_FILTERS). Recording money always needs an explicit
    "done in e-banking" confirmation: Shareomat never moves money itself.
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
    sample_payment_part,
    save_payment_profile,
    set_reminder_block,
)
from shareomat.leg_const import BILLING_STATUS_RELEASED
from shareomat.core.payments.swiss_qr import format_iban, split_street
from shareomat.database.invoicing import _creditor
from shareomat.models.invoicing import (
    PROFILE_STATUS_LABELS,
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
        return render_page(
            "invoices/dispatch.html", ctx, "invoices", run_id=run_id, rows=rows, label=_run_label(ctx, run_id),
            without_email=sorted({r["doc"].recipient_label for r in rows if not r["doc"].recipient_email}))

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
        return jinja_env.get_template("invoices/print.html").render(
            title=reminder.label, body=reminder.snapshot_html, sha256=reminder.snapshot_sha256,
            stamp="" if parent.integrity_ok else _INTEGRITY_STAMP)

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
        get_state().set_flash(f"Versand von {done} Beleg(en) erfasst.", ok=True)
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
    raise FormError("Unbekannte Aktion.")
