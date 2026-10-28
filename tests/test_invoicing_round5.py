# -*- coding: utf-8 -*-
"""Audit round 5 (80b0e7c): cancellation/reminder integrity, Monatsabschluss, Sammelversand, filters, mobile."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from shareomat.database.invoicing import (
    InvoicingError,
    cancel_document,
    confirm_payment_profile,
    create_reminder,
    get_document,
    get_payment_profile,
    issue_documents,
    month_overview,
    record_delivery,
    record_payment,
    record_payout,
    save_payment_profile,
)
from shareomat.database.sqlite import connect
from shareomat.models.invoicing import PaymentProfile
from shareomat.web.pages import dashboard as dashboard_page
from shareomat.web.pages import invoices as invoices_page
from shareomat.web.pages.invoices import render_document
from shareomat.web.rendering import FormError, Redirect, RequestContext
from shareomat.web.state import get_state
from tests.test_invoicing import (  # noqa: F401  (fixtures)
    IBAN,
    ISSUED,
    LATER,
    _SIMPLE,
    _by_participant,
    _ready_profile,
    _released_run,
    _write_month,
    db_path,
    issued,
    runtime,
)


def _get(page, segments=(), query=None):
    return page.handle_get(RequestContext(method="GET", ingress_path="", segments=list(segments), query=query or {}))


def _post(page, segments, form):
    return page.handle_post(RequestContext(method="POST", ingress_path="", segments=list(segments),
                                           form={"csrf_token": get_state().csrf_token, **form}))


# ── P1-01 / P1-02 integrity ─────────────────────────────────────────────────


def test_tampered_cancellation_number_fails_integrity_and_blocks_replacement(db_path, runtime, issued):
    doc = _by_participant(db_path)["cons_b"]
    record_delivery(db_path, doc.id, "paper")
    cancelled = cancel_document(db_path, doc.id, "falsch", render_document, today=ISSUED)
    assert cancelled.integrity_ok and cancelled.cancel_number == "S-2027-001"
    for column, value in (("cancel_number", "S-2027-999"), ("cancel_reason", "anders"),
                          ("cancelled_at", "2020-01-01T00:00:00+00:00")):
        with connect(db_path) as conn:
            with conn:
                original = conn.execute(f"SELECT {column} FROM billing_documents WHERE id = ?", (doc.id,)).fetchone()[0]
                conn.execute(f"UPDATE billing_documents SET {column} = ? WHERE id = ?", (value, doc.id))
        assert not get_document(db_path, doc.id).integrity_ok, column
        with pytest.raises(InvoicingError, match="Prüfsumme"):
            record_delivery(db_path, doc.id, "paper", item="cancellation")
        with connect(db_path) as conn:
            with conn:
                conn.execute(f"UPDATE billing_documents SET {column} = ? WHERE id = ?", (original, doc.id))
    assert get_document(db_path, doc.id).integrity_ok


def test_empty_reminder_qr_payload_fails_integrity(db_path, runtime, issued):
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    reminder = create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=5))
    for column in ("qr_payload", "record_sha256"):
        with connect(db_path) as conn:
            with conn:
                original = conn.execute(f"SELECT {column} FROM document_reminders WHERE id = ?", (reminder.id,)).fetchone()[0]
                conn.execute(f"UPDATE document_reminders SET {column} = '' WHERE id = ?", (reminder.id,))
        assert not get_document(db_path, doc.id).integrity_ok, column
        with connect(db_path) as conn:
            with conn:
                conn.execute(f"UPDATE document_reminders SET {column} = ? WHERE id = ?", (original, reminder.id))
    assert get_document(db_path, doc.id).integrity_ok


def test_tampered_reminder_cannot_print_unmarked(db_path, runtime, issued):
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    reminder = create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=5))
    assert "PRÜFSUMME STIMMT NICHT" not in _get(invoices_page, ["reminders", str(reminder.id), "print"])
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE document_reminders SET open_amount_chf = '0.01' WHERE id = ?", (reminder.id,))
    assert "PRÜFSUMME STIMMT NICHT" in _get(invoices_page, ["reminders", str(reminder.id), "print"])


def test_reminder_mentions_a_changed_bank_account(db_path, runtime, issued):
    """P2-03: same reference type, new IBAN -> the reminder says so explicitly."""
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    save_payment_profile(db_path, PaymentProfile(payee="LEG Test Konto", street="Dorfstrasse", building="1",
                                                 postal_code="4000", city="Basel", iban="CH56 0483 5012 3456 7800 9"))
    confirm_payment_profile(db_path, version=get_payment_profile(db_path).version)
    reminder = create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=5))
    assert "Bankverbindung hat geändert" in reminder.snapshot_html and "CH56" in reminder.qr_payload


# ── P1-03 Monatsabschluss ───────────────────────────────────────────────────


def test_month_overview_leads_through_the_month(db_path, runtime):
    def state():
        m = month_overview(db_path, today=LATER)
        return {s["key"]: s["state"] for s in m["steps"]}, (m["next"]["action"]["label"] if m["next"] else None), m

    steps, nxt, _ = state()
    assert nxt == "Abrechnung berechnen"
    from shareomat.database.billing import compute_billing_preview, release_billing_run, save_draft
    _write_month(runtime, _SIMPLE)
    run = save_draft(db_path, compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31)))
    steps, nxt, m = state()
    assert m["label"] == "Juli 2027" and nxt == "Abrechnung prüfen & freigeben" and steps["issue"] == "waiting"
    release_billing_run(db_path, run.id)
    steps, nxt, m = state()
    assert nxt == "Bankkonto einrichten" and steps["bank"] == "blocked" and steps["issue"] == "todo"
    assert m["invoices_chf"] == m["credits_chf"]
    _ready_profile(db_path)
    steps, nxt, _ = state()
    assert nxt == "3 Beleg(e) prüfen & freigeben" and "bank" not in steps
    issue_documents(db_path, run.id, render_document, today=ISSUED)
    steps, nxt, _ = state()
    assert nxt == "3 Beleg(e) versenden" and steps["issue"] == "done"
    docs = _by_participant(db_path)
    for d in docs.values():
        record_delivery(db_path, d.id, "paper")
    steps, nxt, m = state()
    assert steps["send"] == "done" and steps["payments"] == "todo" and nxt == "Offene Rechnungen"   # overdue on LATER
    record_payment(db_path, docs["cons_a"].id, "0.10", ISSUED, reference="in-a", today=LATER)
    record_payment(db_path, docs["cons_b"].id, "0.10", ISSUED, reference="in-b", today=LATER)
    steps, nxt, m = state()
    assert steps["payments"] == "done" and nxt == "Auszahlungen erfassen" and str(m["payable_chf"]) == "0.20"
    record_payout(db_path, docs["solar"].id, "0.20", ISSUED, reference="out", today=LATER)
    steps, nxt, m = state()
    assert m["closed"] and nxt is None


def test_dashboard_shows_the_monthly_closing(db_path, runtime):
    _released_run(db_path, runtime, _SIMPLE)
    html = _get(dashboard_page)
    assert "Monatsabschluss" in html and "Juli 2027" in html and "Bankkonto einrichten" in html


# ── P1-04 Sammelversand ─────────────────────────────────────────────────────


def test_bulk_dispatch_prints_and_records_without_opening_each_document(db_path, runtime, issued):
    page = _get(invoices_page, ["dispatch"], {"period": str(issued)})
    assert page.count('name="pick_') == 3 and "Ohne E-Mail-Adresse" in page and "Consumer B" in page
    assert " checked" not in page.split("<tbody>")[1].split("</tbody>")[0]      # round 6 P1-04: nothing pre-ticked
    printed = _get(invoices_page, ["runs", str(issued), "print-all"])
    assert printed.count('class="page') == 3 and printed.count("Empfangsschein") == 2     # 2 invoices, 1 credit
    docs = _by_participant(db_path)
    picks = {f"pick_d{docs['cons_a'].id}": "on", f"method_d{docs['cons_a'].id}": "email",
             f"pick_d{docs['solar'].id}": "on", f"method_d{docs['solar'].id}": "paper"}
    with pytest.raises(FormError, match="tatsächlich versendet"):
        _post(invoices_page, ["dispatch"], {"run_id": str(issued), **picks})
    with pytest.raises(Redirect):
        _post(invoices_page, ["dispatch"], {"run_id": str(issued), "confirm": "on", **picks})
    assert get_document(db_path, docs["cons_a"].id).deliveries[0].method == "email"
    assert get_document(db_path, docs["solar"].id).sent_at is not None
    assert get_document(db_path, docs["cons_b"].id).sent_at is None
    assert _get(invoices_page, ["dispatch"], {"period": str(issued)}).count('name="pick_') == 1


def test_bulk_dispatch_includes_pending_cancellation_notices(db_path, runtime, issued):
    doc = _by_participant(db_path)["cons_b"]
    record_delivery(db_path, doc.id, "paper")
    cancel_document(db_path, doc.id, "falsch", render_document, today=ISSUED)
    page = _get(invoices_page, ["dispatch"], {"period": str(issued)})
    assert f'name="pick_c{doc.id}"' in page and "Stornobeleg S-2027-001" in page


# ── P1-05 / P1-07 lists ─────────────────────────────────────────────────────


def test_lists_filter_by_month_and_status_with_kpis(db_path, runtime, issued):
    docs = _by_participant(db_path)
    record_delivery(db_path, docs["cons_a"].id, "paper")
    html = _get(invoices_page, query={"tab": "invoices", "status": "to_send"})
    assert "Juli 2027" in html and docs["cons_b"].number in html and docs["cons_a"].number not in html
    assert "Versendet" in html and "Offen CHF" in html and 'class="cards"' in html and 'data-label="Status"' in html
    record_payment(db_path, docs["cons_a"].id, "0.10", ISSUED, reference="in-a", today=LATER)
    html = _get(invoices_page, query={"tab": "credits", "status": "payable"})
    assert docs["solar"].number in html
    html = _get(invoices_page, query={"tab": "invoices", "status": "done"})
    assert docs["cons_a"].number in html and docs["cons_b"].number not in html
    assert docs["cons_a"].number in _get(invoices_page, query={"tab": "invoices", "period": "all"})


def test_mobile_menu_collapses_and_tables_become_cards(db_path, runtime, issued):
    html = _get(invoices_page)
    assert 'id="nav-toggle"' in html and "☰ Menü · <strong>Rechnungen</strong>" in html
    from pathlib import Path
    css = (Path(__file__).resolve().parents[1] / "shareomat/web/static/app.css").read_text(encoding="utf-8")
    assert "table.cards td::before" in css and ".nav-toggle-input:checked ~ .nav-body" in css


# ── P2-02 explicit bank confirmation ────────────────────────────────────────


def test_money_bookings_need_the_bank_confirmation(db_path, runtime, issued):
    doc = _by_participant(db_path)["cons_a"]
    with pytest.raises(FormError, match="eingegangen"):
        _post(invoices_page, [str(doc.id), "payment"], {"amount": "0.10", "reference": "in-1"})
    assert get_document(db_path, doc.id).payments == []
