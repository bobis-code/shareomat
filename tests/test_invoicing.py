# -*- coding: utf-8 -*-
"""Rechnungen & Gutschriften (shareomat.database.invoicing + web page), incl. the audit of 27.10.2026."""

from __future__ import annotations

import csv
import threading
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.billing import (
    BillingWorkflowError,
    cancel_billing_run,
    compute_billing_preview,
    release_billing_run,
    save_draft,
)
from shareomat.database.community import save_community
from shareomat.database.invoicing import (
    InvoicingError,
    _floor_shares,
    cancel_document,
    create_reminder,
    discard_reminder,
    get_document,
    iban_is_valid,
    issue_documents,
    issue_problems,
    list_documents,
    payout_status,
    record_delivery,
    record_payment,
    record_payout,
    record_refund,
    reminder_proposals,
    save_payment_details,
    set_reminder_block,
)
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant, update_participant
from shareomat.database.sqlite import connect, init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff
from shareomat.web.pages import invoices as invoices_page
from shareomat.web.pages.invoices import render_document
from shareomat.web.rendering import FormError, Redirect, RequestContext
from shareomat.web.state import get_state

IBAN = "CH93 0076 2011 6238 5295 7"   # valid example IBAN
ISSUED = date(2027, 8, 5)
LATER = date(2027, 12, 31)            # "today" for bookings in the tests


def _addr(pid: str, label: str, email: str = "", street: str = "Weg 1") -> Participant:
    return Participant(pid, label, email=email, address_line=street, postal_code="4000", city="Basel")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    save_community(path, Community(community_id="ZEV-001", name="LEG Test", address_line="Dorf 1",
                                   postal_code="4000", city="Basel"))
    create_participant(path, _addr("solar", "Solar PV"))
    create_participant(path, _addr("cons_a", "Consumer A", email="a@example.ch"))
    create_participant(path, _addr("cons_b", "Consumer B"))
    create_participant(path, _addr("prosumer", "Prosumer"))
    create_meter(path, Meter("M1", "solar", "PV", "producer"))
    create_meter(path, Meter("M2", "cons_a", "Bezug A", "consumer"))
    create_meter(path, Meter("M4", "cons_b", "Bezug B", "consumer"))
    create_meter(path, Meter("M3", "prosumer", "Haus", "producer_consumer"))
    create_tariff(path, Tariff(local_rate_chf_kwh=Decimal("0.10"), grid_rate_chf_kwh=Decimal("0"),
                               feed_in_rate_chf_kwh=Decimal("0.10"), valid_from=date(2020, 1, 1)))
    return path


@pytest.fixture
def runtime(tmp_path, db_path):
    for name in ("inbox", "archive", "reports", "state"):
        (tmp_path / name).mkdir()
    runtime = RuntimeConfig(paths=PathConfig(inbox=tmp_path / "inbox", archive=tmp_path / "archive",
                                             reports=tmp_path / "reports", state=tmp_path / "state"))
    get_state().register_runtime(runtime)
    return runtime


_CHANNELS = [("M1", "export"), ("M2", "import"), ("M4", "import"), ("M3", "import"), ("M3", "export")]


def _write_month(runtime: RuntimeConfig, values: dict[tuple[str, str], str], channels=_CHANNELS) -> None:
    """A complete July 2027 (every quarter hour, every channel); `values` go into the first slot, zeros elsewhere."""
    start = datetime(2027, 6, 30, 22, 0, tzinfo=timezone.utc)
    end = datetime(2027, 7, 31, 22, 0, tzinfo=timezone.utc)
    with (runtime.paths.inbox / "jul.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        slot, first = start, True
        while slot < end:
            ts = slot.isoformat()
            for meter, direction in channels:
                writer.writerow([ts, meter, values.get((meter, direction), "0") if first else "0", direction])
            slot += timedelta(minutes=15)
            first = False


def _released_run(db_path: Path, runtime: RuntimeConfig, values: dict, *, override: str = "", channels=_CHANNELS) -> int:
    _write_month(runtime, values, channels)
    preview = compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31))
    assert override or not preview.availability.blocking_issues, preview.availability.blocking_issues
    run = save_draft(db_path, preview)
    release_billing_run(db_path, run.id, override_reason=override)
    return run.id


# solar delivers 2 kWh, A and B consume 1 kWh each -> 0.10 each, credit 0.20; prosumer idle.
_SIMPLE = {("M1", "export"): "2.0", ("M2", "import"): "1.0", ("M4", "import"): "1.0"}


def _by_participant(db_path):
    return {d.participant_id: d for d in list_documents(db_path) if d.status != "cancelled"}


@pytest.fixture
def issued(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    return run_id


def test_iban_check():
    assert iban_is_valid(IBAN)
    assert not iban_is_valid("CH93 0076 2011 6238 5295 8")
    assert not iban_is_valid("CH93 0076")


# ── issuing ─────────────────────────────────────────────────────────────────


def test_issue_requires_payment_details(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    assert any("Zahlungsangaben" in p for p in issue_problems(db_path, run_id))
    with pytest.raises(InvoicingError):
        issue_documents(db_path, run_id, render_document, today=ISSUED)
    assert list_documents(db_path) == []


def test_overridden_release_is_never_invoiced(db_path, runtime):
    """P0-06: a run released despite gaps (one day of data) stays an internal evaluation."""
    with (runtime.paths.inbox / "jul.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        for meter, direction in _CHANNELS:
            writer.writerow(["2027-07-01T10:00:00+00:00", meter, _SIMPLE.get((meter, direction), "0"), direction])
    run = save_draft(db_path, compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31)))
    release_billing_run(db_path, run.id, override_reason="nur ein Tag")
    save_payment_details(db_path, "LEG Test", IBAN)
    assert any("interne Auswertung" in p for p in issue_problems(db_path, run.id))
    with pytest.raises(InvoicingError, match="interne Auswertung"):
        issue_documents(db_path, run.id, render_document, today=ISSUED)


def test_issue_creates_numbered_frozen_documents_once(db_path, runtime, issued):
    docs = _by_participant(db_path)
    assert sorted(d.number for d in docs.values()) == ["G-2027-001", "R-2027-001", "R-2027-002"]
    assert set(docs) == {"solar", "cons_a", "cons_b"}           # idle prosumer gets nothing
    assert docs["cons_a"].amount_chf == Decimal("0.10") and docs["cons_a"].due_date == ISSUED + timedelta(days=30)
    assert docs["solar"].doc_type == "credit" and docs["solar"].amount_chf == Decimal("0.20")
    html = docs["cons_a"].snapshot_html
    assert docs["cons_a"].integrity_ok
    assert "CH93 0076 2011 6238 5295 7" in html and "Ziff. 3.2" in html
    assert issue_documents(db_path, issued, render_document, today=ISSUED) == []
    with pytest.raises(BillingWorkflowError, match="zuerst die Belege stornieren"):
        cancel_billing_run(db_path, issued)


def test_recipient_comes_from_the_billing_snapshot(db_path, runtime):
    """P0-05: master data changed after the draft was saved never reaches the documents."""
    _write_month(runtime, _SIMPLE)
    run = save_draft(db_path, compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31)))
    update_participant(db_path, "cons_a", _addr("cons_a", "Neue Mieterin", street="Neuweg 9"))
    release_billing_run(db_path, run.id)
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run.id, render_document, today=ISSUED)
    doc = _by_participant(db_path)["cons_a"]
    assert doc.recipient_label == "Consumer A" and "Weg 1" in doc.recipient_address
    assert "Neue Mieterin" not in doc.snapshot_html and "Neuweg" not in doc.snapshot_html


def test_missing_address_at_billing_time_blocks(db_path, runtime):
    update_participant(db_path, "cons_b", Participant("cons_b", "Consumer B"))
    run_id = _released_run(db_path, runtime, _SIMPLE)
    save_payment_details(db_path, "LEG Test", IBAN)
    update_participant(db_path, "cons_b", _addr("cons_b", "Consumer B"))     # too late: snapshot counts
    assert any("Consumer B: Postadresse fehlte" in p for p in issue_problems(db_path, run_id))


def test_prosumer_netting_and_zero_balance(db_path, runtime):
    """§3.4 netting; P1-11: an exactly balanced prosumer gets no 0.00 document."""
    run_id = _released_run(db_path, runtime, {("M2", "import"): "1.0", ("M3", "export"): "1.0"})
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    docs = _by_participant(db_path)
    assert set(docs) == {"cons_a", "prosumer"}
    assert docs["prosumer"].doc_type == "credit" and docs["prosumer"].amount_chf == Decimal("0.10")


def test_balanced_prosumer_gets_no_document(db_path, runtime):
    # prosumer imports 1 and exports 1 in the same slot: LEG gives it 1 kWh from itself -> charge 0.10, credit 0.10
    run_id = _released_run(db_path, runtime, {("M3", "import"): "1.0", ("M3", "export"): "1.0"})
    save_payment_details(db_path, "LEG Test", IBAN)
    assert issue_documents(db_path, run_id, render_document, today=ISSUED) == []


# ── payments and payouts ────────────────────────────────────────────────────


def test_floor_shares_are_monotone_and_never_exceed_the_pool():
    credits = [Decimal("0.01"), Decimal("0.03"), Decimal("0.03")]
    previous = [Decimal(0)] * 3
    for cents in range(0, 8):
        pool = Decimal(cents) / 100
        shares = _floor_shares(pool, credits)
        assert sum(shares) <= pool
        assert all(s >= p for s, p in zip(shares, previous)), (pool, shares, previous)
        assert all(s <= c for s, c in zip(shares, credits))
        previous = shares
    assert previous == credits


def test_payouts_never_exceed_received_payments(db_path, runtime):
    """P0-01 reproduced: credits 0.01/0.03/0.03, payments arriving in single cents."""
    create_participant(db_path, _addr("solar2", "Solar 2"))
    create_meter(db_path, Meter("M5", "solar2", "PV 2", "producer"))
    channels = _CHANNELS + [("M5", "export")]
    run_id = _released_run(db_path, runtime, {("M1", "export"): "0.1", ("M3", "export"): "0.3",
                                              ("M5", "export"): "0.3", ("M2", "import"): "0.3",
                                              ("M4", "import"): "0.4"}, channels=channels)
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    docs = _by_participant(db_path)
    credits = [d for d in docs.values() if d.doc_type == "credit"]
    assert sorted(c.amount_chf for c in credits) == [Decimal("0.01"), Decimal("0.03"), Decimal("0.03")]
    received = Decimal(0)
    for n, (pid, cents) in enumerate([("cons_a", "0.03"), ("cons_b", "0.01"), ("cons_b", "0.03")]):
        record_payment(db_path, docs[pid].id, cents, ISSUED, reference=f"bank-{n}", today=LATER)
        received += Decimal(cents)
        for c in credits:
            available = payout_status(db_path, run_id)[c.id].available_chf
            if available:
                record_payout(db_path, c.id, str(available), ISSUED, reference=f"out-{n}-{c.id}", today=LATER)
        paid_out = sum(get_document(db_path, c.id).booked_chf for c in credits)
        assert paid_out <= received
    assert paid_out == received == Decimal("0.07")


def test_concurrent_payouts_cannot_both_pass(db_path, runtime, issued):
    """P0-02: two simultaneous payouts of the same available CHF 0.10 -> exactly one is booked."""
    docs = _by_participant(db_path)
    record_payment(db_path, docs["cons_a"].id, "0.10", ISSUED, reference="bank-1", today=LATER)
    barrier, results = threading.Barrier(2), []

    def payout(n):
        barrier.wait()
        try:
            record_payout(db_path, docs["solar"].id, "0.10", ISSUED, reference=f"out-{n}", today=LATER)
            results.append("ok")
        except InvoicingError:
            results.append("refused")

    threads = [threading.Thread(target=payout, args=(n,)) for n in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "refused"]
    assert get_document(db_path, docs["solar"].id).booked_chf == Decimal("0.10")


def test_double_booking_of_one_transfer_needs_confirmation(db_path, runtime, issued):
    """P0-03: the same bank reference twice is refused unless explicitly confirmed with a reason."""
    docs = _by_participant(db_path)
    record_payment(db_path, docs["cons_a"].id, "0.05", ISSUED, reference="Bank 4711", today=LATER)
    with pytest.raises(InvoicingError, match="Doppelbuchung"):
        record_payment(db_path, docs["cons_a"].id, "0.05", ISSUED, reference="bank 4711 ", today=LATER)
    with pytest.raises(InvoicingError, match="Doppelbuchung"):
        record_payment(db_path, docs["cons_b"].id, "0.05", ISSUED, reference="Bank 4711", today=LATER)
    with pytest.raises(InvoicingError, match="Begründung"):
        record_payment(db_path, docs["cons_a"].id, "0.05", ISSUED, reference="Bank 4711", today=LATER,
                       confirm_duplicate=True)
    record_payment(db_path, docs["cons_a"].id, "0.05", ISSUED, reference="Bank 4711", today=LATER,
                   confirm_duplicate=True, note="zwei Teilzahlungen mit gleicher Referenz")
    with pytest.raises(InvoicingError, match="Referenz"):
        record_payment(db_path, docs["cons_b"].id, "0.10", ISSUED, reference="", today=LATER)


def test_payment_corrections_and_refunds(db_path, runtime, issued):
    """Corrections need a reason and can't take back money already passed on; refunds (P1-04) only up to the over-payment."""
    docs = _by_participant(db_path)
    credit = docs["solar"]
    record_payment(db_path, docs["cons_a"].id, "0.15", ISSUED, reference="bank-1", today=LATER)
    assert payout_status(db_path, issued)[credit.id].available_chf == Decimal("0.10")
    with pytest.raises(InvoicingError, match="Bankreferenz"):
        record_payout(db_path, credit.id, "0.10", ISSUED, reference="", today=LATER)
    record_payout(db_path, credit.id, "0.10", ISSUED, reference="out-1", today=LATER)
    with pytest.raises(InvoicingError, match="weitergegeben"):
        record_payment(db_path, docs["cons_a"].id, "-0.10", ISSUED, note="Fehlbuchung", today=LATER)
    with pytest.raises(InvoicingError, match="Begründung"):
        record_payment(db_path, docs["cons_a"].id, "-0.05", ISSUED, today=LATER)
    with pytest.raises(InvoicingError, match="höchstens die Überzahlung"):
        record_refund(db_path, docs["cons_a"].id, "0.06", ISSUED, reference="ret-1", today=LATER)
    record_refund(db_path, docs["cons_a"].id, "0.05", ISSUED, reference="ret-1", today=LATER)
    doc = get_document(db_path, docs["cons_a"].id)
    assert doc.state(LATER) == "paid" and doc.booked_chf == Decimal("0.10")
    assert payout_status(db_path, issued)[credit.id].entitled_chf == Decimal("0.10")


def test_booking_dates_are_checked(db_path, runtime, issued):
    """P2-01: no bookings in the future or before the document date."""
    doc = _by_participant(db_path)["cons_a"]
    with pytest.raises(InvoicingError, match="Zukunft"):
        record_payment(db_path, doc.id, "0.10", LATER + timedelta(days=1), reference="b", today=LATER)
    with pytest.raises(InvoicingError, match="vor dem Belegdatum"):
        record_payment(db_path, doc.id, "0.10", ISSUED - timedelta(days=1), reference="b", today=LATER)


# ── cancelling ──────────────────────────────────────────────────────────────


def test_undelivered_document_is_simply_cancelled_and_reissued(db_path, runtime, issued):
    docs = _by_participant(db_path)
    record_payment(db_path, docs["cons_a"].id, "0.10", ISSUED, reference="b-1", today=LATER)
    with pytest.raises(InvoicingError, match="gebucht"):
        cancel_document(db_path, docs["cons_a"].id, "x", render_document)
    with pytest.raises(InvoicingError, match="Stornogrund"):
        cancel_document(db_path, docs["cons_b"].id, " ", render_document)
    cancelled = cancel_document(db_path, docs["cons_b"].id, "Betrag falsch", render_document)
    assert cancelled.cancel_number == ""
    with pytest.raises(InvoicingError):
        record_payment(db_path, docs["cons_b"].id, "0.10", ISSUED, reference="b-2", today=LATER)
    again = issue_documents(db_path, issued, render_document, today=ISSUED)
    assert [d.number for d in again] == ["R-2027-003"] and again[0].replaces_number == "R-2027-002"


def test_delivered_document_needs_a_delivered_cancellation_notice(db_path, runtime, issued):
    """P0-04: after delivery only a numbered Stornobeleg cancels; the replacement waits for its delivery."""
    doc = _by_participant(db_path)["cons_b"]
    record_delivery(db_path, doc.id, "paper")
    cancelled = cancel_document(db_path, doc.id, "falscher Tarif", render_document, today=ISSUED)
    assert cancelled.cancel_number == "S-2027-001" and doc.number in cancelled.cancel_snapshot_html
    assert cancelled.integrity_ok
    with pytest.raises(InvoicingError, match="Stornobeleg zu R-2027-002"):
        issue_documents(db_path, issued, render_document, today=ISSUED)
    with pytest.raises(InvoicingError, match="nicht mehr versendet"):
        record_delivery(db_path, doc.id, "paper")
    record_delivery(db_path, doc.id, "paper", item="cancellation")
    replacement = issue_documents(db_path, issued, render_document, today=ISSUED)[0]
    assert replacement.replaces_number == "R-2027-002"
    assert "Ersetzt Rechnung R-2027-002" in replacement.snapshot_html and "S-2027-001" in replacement.snapshot_html


# ── reminders ───────────────────────────────────────────────────────────────


def test_reminders_follow_due_date_delivery_and_block(db_path, runtime, issued):
    doc = _by_participant(db_path)["cons_a"]
    due = doc.due_date
    assert reminder_proposals(db_path, today=due + timedelta(days=10)) == []     # not delivered yet
    with pytest.raises(InvoicingError, match="keine E-Mail"):
        record_delivery(db_path, _by_participant(db_path)["cons_b"].id, "email")
    record_delivery(db_path, doc.id, "email", detail="manuell versendet")
    record_delivery(db_path, doc.id, "email", detail="nochmals")                  # resend: log only
    assert len(list_documents(db_path, doc_type="invoice")) == 2
    assert reminder_proposals(db_path, today=due + timedelta(days=4)) == []
    assert [(p.document.id, p.level) for p in reminder_proposals(db_path, today=due + timedelta(days=5))] == [(doc.id, 1)]

    reminder = create_reminder(db_path, doc.id, render_document, today=due + timedelta(days=5))
    assert reminder.label == "Zahlungserinnerung" and reminder.open_amount_chf == Decimal("0.10")
    assert "Weg 1" in reminder.snapshot_html and reminder.new_due_date == due + timedelta(days=15)
    assert reminder_proposals(db_path, today=due + timedelta(days=25)) == []    # level 1 not sent yet
    record_delivery(db_path, doc.id, "paper", item="reminder", reminder_id=reminder.id)
    assert [p.level for p in reminder_proposals(db_path, today=due + timedelta(days=25))] == [2]

    set_reminder_block(db_path, doc.id, "Ratenzahlung vereinbart")
    assert reminder_proposals(db_path, today=due + timedelta(days=25)) == []
    with pytest.raises(InvoicingError):
        create_reminder(db_path, doc.id, render_document, today=due + timedelta(days=25))
    set_reminder_block(db_path, doc.id, "")
    record_payment(db_path, doc.id, "0.10", due + timedelta(days=21), reference="b-1", today=LATER)
    assert reminder_proposals(db_path, today=due + timedelta(days=25)) == []


def test_next_reminder_waits_for_the_new_deadline(db_path, runtime, issued):
    """P1-02: a late first reminder sets a 10-day deadline; level 2 only after it + 5 days grace."""
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    first_day = doc.due_date + timedelta(days=40)
    reminder = create_reminder(db_path, doc.id, render_document, today=first_day)
    record_delivery(db_path, doc.id, "paper", item="reminder", reminder_id=reminder.id)
    assert reminder_proposals(db_path, today=first_day) == []
    assert reminder_proposals(db_path, today=first_day + timedelta(days=14)) == []
    assert [p.level for p in reminder_proposals(db_path, today=first_day + timedelta(days=15))] == [2]


def test_reminder_is_not_sent_after_payment(db_path, runtime, issued):
    """P1-01: paid or partly paid since the reminder was created -> sending refused; discard and recreate."""
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    day = doc.due_date + timedelta(days=5)
    reminder = create_reminder(db_path, doc.id, render_document, today=day)
    record_payment(db_path, doc.id, "0.04", ISSUED, reference="part", today=LATER)
    with pytest.raises(InvoicingError, match="offene Betrag geändert"):
        record_delivery(db_path, doc.id, "paper", item="reminder", reminder_id=reminder.id)
    discard_reminder(db_path, reminder.id, "Teilzahlung eingegangen")
    second = create_reminder(db_path, doc.id, render_document, today=day)
    assert second.level == 1 and second.open_amount_chf == Decimal("0.06")
    record_payment(db_path, doc.id, "0.06", ISSUED, reference="rest", today=LATER)
    with pytest.raises(InvoicingError, match="bezahlt"):
        record_delivery(db_path, doc.id, "paper", item="reminder", reminder_id=second.id)


def test_tampered_snapshot_is_detected(db_path, runtime, issued):
    """P2-05: the stored text is re-checked against its SHA-256 on every read."""
    doc = _by_participant(db_path)["cons_a"]
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE billing_documents SET snapshot_html = snapshot_html || ' ' WHERE id = ?", (doc.id,))
    assert not get_document(db_path, doc.id).integrity_ok
    printed = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[str(doc.id), "print"]))
    assert "PRÜFSUMME STIMMT NICHT" in printed


# ── web page ────────────────────────────────────────────────────────────────


def test_invoices_page_workflow(db_path, runtime):
    from shareomat.web.navigation import PLACEHOLDER_ROUTES
    assert "invoices" not in PLACEHOLDER_ROUTES
    run_id = _released_run(db_path, runtime, _SIMPLE)
    csrf = get_state().csrf_token

    html = invoices_page.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "Zahlungsangaben (Kontoinhaber, IBAN) fehlen" in html

    with pytest.raises(FormError, match="IBAN ist ungültig"):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="", segments=["settings"],
                                                 form={"csrf_token": csrf, "payee": "LEG", "iban": "CH00"}))
    with pytest.raises(Redirect):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="", segments=["settings"],
                                                 form={"csrf_token": csrf, "payee": "LEG Test", "iban": IBAN}))
    html = invoices_page.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "Vorschau &amp; Freigabe" in html
    preview = invoices_page.handle_get(RequestContext(method="GET", ingress_path="",
                                                      segments=["runs", str(run_id), "preview"]))
    assert "Consumer A" in preview and "Freigeben &amp; Belege erstellen" in preview and "0.20" in preview
    assert list_documents(db_path) == []                                  # preview stores nothing
    with pytest.raises(FormError, match="bestätigen"):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="", segments=["issue"],
                                                 form={"csrf_token": csrf, "run_id": str(run_id)}))
    with pytest.raises(Redirect):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="", segments=["issue"],
                                                 form={"csrf_token": csrf, "run_id": str(run_id), "confirm": "on"}))
    for tab in ("invoices", "credits", "reminders", "settings"):
        html = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", query={"tab": tab}))
        assert "Rechnungen &amp; Gutschriften" in html
    html = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", query={"tab": "invoices"}))
    assert "Consumer A" in html and "Freigegebene Abrechnungen ohne Belege" not in html

    docs = _by_participant(db_path)
    a = str(docs["cons_a"].id)
    detail = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[a]))
    assert "Zahlung erfassen" in detail and "Mahnwesen" in detail and "Zweite Buchung bestätigt" in detail
    credit = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[str(docs["solar"].id)]))
    assert "Auszahlung erfassen" in credit and "Jetzt auszahlbar" in credit

    with pytest.raises(Redirect):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="", segments=[a, "delivery"],
                                                 form={"csrf_token": csrf, "method": "paper", "target": "document"}))
    with pytest.raises(Redirect):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="", segments=[a, "cancel"],
                                                 form={"csrf_token": csrf, "reason": "Adresse"}))
    cancelled = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[a]))
    assert "Stornobeleg S-" in cancelled and "noch nicht zugestellt" in cancelled
    storno = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[a, "print", "cancellation"]))
    assert "wird hiermit vollständig storniert" in storno
    with pytest.raises(Redirect):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="", segments=[a, "delivery"],
                                                 form={"csrf_token": csrf, "method": "paper", "target": "cancellation"}))
    with pytest.raises(FormError, match="Ziff. 3.4"):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="",
                                                 segments=[str(docs["solar"].id), "payout"],
                                                 form={"csrf_token": csrf, "amount": "0.20", "reference": "X"}))
