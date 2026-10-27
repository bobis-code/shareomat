# -*- coding: utf-8 -*-
"""Rechnungen & Gutschriften (shareomat.database.invoicing + web page)."""

from __future__ import annotations

import csv
import hashlib
from datetime import date, timedelta
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
    cancel_document,
    create_reminder,
    iban_is_valid,
    issue_documents,
    issue_problems,
    list_documents,
    payout_status,
    record_delivery,
    record_payment,
    record_payout,
    reminder_proposals,
    save_payment_details,
    set_reminder_block,
)
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant, get_participant, update_participant
from shareomat.database.sqlite import init_db
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


def _addr(pid: str, label: str, email: str = "") -> Participant:
    return Participant(pid, label, email=email, address_line="Weg 1", postal_code="4000", city="Basel")


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


def _released_run(db_path: Path, runtime: RuntimeConfig, rows: list[tuple]) -> int:
    with (runtime.paths.inbox / "jul.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        writer.writerows(rows)
    run = save_draft(db_path, compute_billing_preview(db_path, runtime, date(2027, 7, 1), date(2027, 7, 31)))
    release_billing_run(db_path, run.id, override_reason="Testdaten (eine Viertelstunde)")
    return run.id


# solar delivers 2 kWh, A and B consume 1 kWh each -> 0.10 each, credit 0.20; prosumer idle.
_SIMPLE = [("2027-07-01T10:00:00+00:00", "M1", "2.0", "export"),
           ("2027-07-01T10:00:00+00:00", "M2", "1.0", "import"),
           ("2027-07-01T10:00:00+00:00", "M4", "1.0", "import"),
           ("2027-07-01T10:00:00+00:00", "M3", "0", "import"),
           ("2027-07-01T10:00:00+00:00", "M3", "0", "export")]


def _by_participant(db_path):
    return {d.participant_id: d for d in list_documents(db_path) if d.status != "cancelled"}


def test_iban_check():
    assert iban_is_valid(IBAN)
    assert not iban_is_valid("CH93 0076 2011 6238 5295 8")
    assert not iban_is_valid("CH93 0076")


def test_issue_requires_payment_details_and_addresses(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    assert any("Zahlungsangaben" in p for p in issue_problems(db_path, run_id))
    save_payment_details(db_path, "LEG Test", IBAN)
    update_participant(db_path, "cons_b", Participant("cons_b", "Consumer B"))   # address removed
    with pytest.raises(InvoicingError, match="Consumer B: Postadresse"):
        issue_documents(db_path, run_id, render_document, today=ISSUED)
    assert list_documents(db_path) == []   # all or nothing


def test_issue_creates_numbered_frozen_documents_once(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    save_payment_details(db_path, "LEG Test", IBAN)
    created = issue_documents(db_path, run_id, render_document, today=ISSUED)
    assert sorted(d.number for d in created) == ["G-2027-001", "R-2027-001", "R-2027-002"]
    docs = _by_participant(db_path)
    assert set(docs) == {"solar", "cons_a", "cons_b"}           # idle prosumer gets nothing
    assert docs["cons_a"].amount_chf == Decimal("0.10") and docs["cons_a"].due_date == ISSUED + timedelta(days=30)
    assert docs["solar"].doc_type == "credit" and docs["solar"].amount_chf == Decimal("0.20")
    assert docs["solar"].due_date is None
    html = docs["cons_a"].snapshot_html
    assert hashlib.sha256(html.encode("utf-8")).hexdigest() == docs["cons_a"].snapshot_sha256
    assert "CH93 0076 2011 6238 5295 7" in html and "R-2027-00" in html and "Ziff. 3.2" in html

    # Later master-data edits never touch an issued document; a second issue creates nothing.
    update_participant(db_path, "cons_a", _addr("cons_a", "Umbenannt"))
    assert issue_documents(db_path, run_id, render_document, today=ISSUED) == []
    assert _by_participant(db_path)["cons_a"].snapshot_html == html
    with pytest.raises(BillingWorkflowError, match="zuerst die Belege stornieren"):
        cancel_billing_run(db_path, run_id)


def test_prosumer_gets_one_netted_document(db_path, runtime):
    rows = [("2027-07-01T10:00:00+00:00", "M1", "0", "export"),
            ("2027-07-01T10:00:00+00:00", "M2", "1.0", "import"),
            ("2027-07-01T10:00:00+00:00", "M4", "0", "import"),
            ("2027-07-01T10:00:00+00:00", "M3", "0", "import"),
            ("2027-07-01T10:00:00+00:00", "M3", "1.0", "export")]
    run_id = _released_run(db_path, runtime, rows)
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    docs = _by_participant(db_path)
    assert set(docs) == {"cons_a", "prosumer"}
    assert docs["prosumer"].doc_type == "credit" and docs["prosumer"].amount_chf == Decimal("0.10")


def test_payout_only_from_received_payments_pro_rata(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    docs = _by_participant(db_path)
    credit = docs["solar"]
    assert payout_status(db_path, run_id)[credit.id].available_chf == 0
    with pytest.raises(InvoicingError, match="Ziff. 3.4"):
        record_payout(db_path, credit.id, "0.20", ISSUED, reference="E-Banking 1")

    record_payment(db_path, docs["cons_a"].id, "0.15", ISSUED)            # over-payment: only 0.10 counts
    assert payout_status(db_path, run_id)[credit.id].available_chf == Decimal("0.10")
    with pytest.raises(InvoicingError, match="Bankreferenz"):
        record_payout(db_path, credit.id, "0.10", ISSUED, reference="")
    record_payout(db_path, credit.id, "0.10", ISSUED, reference="E-Banking 1")
    assert payout_status(db_path, run_id)[credit.id].available_chf == 0
    assert list_documents(db_path, run_id=run_id)[0] is not None
    # The received money was passed on — reversing that payment is refused.
    with pytest.raises(InvoicingError, match="weitergegeben"):
        record_payment(db_path, docs["cons_a"].id, "-0.10", ISSUED, note="Fehlbuchung")
    # A note is mandatory for a correction.
    with pytest.raises(InvoicingError, match="Begründung"):
        record_payment(db_path, docs["cons_a"].id, "-0.05", ISSUED)
    record_payment(db_path, docs["cons_a"].id, "-0.05", ISSUED, note="Überzahlung zurückerstattet")

    record_payment(db_path, docs["cons_b"].id, "0.10", ISSUED)
    status = payout_status(db_path, run_id)[credit.id]
    assert (status.entitled_chf, status.available_chf) == (Decimal("0.20"), Decimal("0.10"))
    states = {d.participant_id: d.state(ISSUED) for d in list_documents(db_path)}
    assert states == {"cons_a": "paid", "cons_b": "paid", "solar": "payout_partial"}


def test_payout_split_between_producers_by_largest_remainder(db_path, runtime):
    # solar 1 kWh + prosumer 2 kWh supply; A and B consume 1.5 each -> credits 0.10 / 0.20, invoices 0.15 / 0.15
    rows = [("2027-07-01T10:00:00+00:00", "M1", "1.0", "export"),
            ("2027-07-01T10:00:00+00:00", "M2", "1.5", "import"),
            ("2027-07-01T10:00:00+00:00", "M4", "1.5", "import"),
            ("2027-07-01T10:00:00+00:00", "M3", "0", "import"),
            ("2027-07-01T10:00:00+00:00", "M3", "2.0", "export")]
    run_id = _released_run(db_path, runtime, rows)
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    docs = _by_participant(db_path)
    assert (docs["solar"].amount_chf, docs["prosumer"].amount_chf) == (Decimal("0.10"), Decimal("0.20"))
    record_payment(db_path, docs["cons_a"].id, "0.15", ISSUED)   # half of the money arrived
    status = payout_status(db_path, run_id)
    shares = (status[docs["solar"].id].entitled_chf, status[docs["prosumer"].id].entitled_chf)
    assert sum(shares) == Decimal("0.15") and shares == (Decimal("0.05"), Decimal("0.10"))


def test_cancel_and_reissue_gets_a_new_number(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    doc = _by_participant(db_path)["cons_b"]
    record_payment(db_path, _by_participant(db_path)["cons_a"].id, "0.10", ISSUED)
    with pytest.raises(InvoicingError, match="Stornogrund"):
        cancel_document(db_path, doc.id, " ")
    cancel_document(db_path, doc.id, "Adresse falsch")
    with pytest.raises(InvoicingError):
        record_payment(db_path, doc.id, "0.10", ISSUED)
    with pytest.raises(InvoicingError, match="gebucht"):
        cancel_document(db_path, _by_participant(db_path)["cons_a"].id, "x")
    again = issue_documents(db_path, run_id, render_document, today=ISSUED)
    assert [d.number for d in again] == ["R-2027-003"]


def test_reminders_follow_due_date_delivery_and_block(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    save_payment_details(db_path, "LEG Test", IBAN)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    doc = _by_participant(db_path)["cons_a"]
    due = doc.due_date
    assert reminder_proposals(db_path, today=due + timedelta(days=10)) == []     # not delivered yet
    with pytest.raises(InvoicingError, match="keine E-Mail"):
        record_delivery(db_path, _by_participant(db_path)["cons_b"].id, "email")
    record_delivery(db_path, doc.id, "email", detail="manuell versendet")
    record_delivery(db_path, doc.id, "email", detail="nochmals")                  # resend: log only
    assert len(list_documents(db_path, doc_type="invoice")) == 2
    assert reminder_proposals(db_path, today=due + timedelta(days=4)) == []
    proposals = reminder_proposals(db_path, today=due + timedelta(days=5))
    assert [(p.document.id, p.level) for p in proposals] == [(doc.id, 1)]

    reminder = create_reminder(db_path, doc.id, render_document, today=due + timedelta(days=5))
    assert reminder.label == "Zahlungserinnerung" and reminder.open_amount_chf == Decimal("0.10")
    assert "R-2027-00" in reminder.snapshot_html and "Basel" in reminder.snapshot_html
    assert reminder_proposals(db_path, today=due + timedelta(days=25)) == []    # level 1 not sent yet
    record_delivery(db_path, doc.id, "paper", reminder_id=reminder.id)
    assert [p.level for p in reminder_proposals(db_path, today=due + timedelta(days=25))] == [2]

    set_reminder_block(db_path, doc.id, "Ratenzahlung vereinbart")
    assert reminder_proposals(db_path, today=due + timedelta(days=25)) == []
    with pytest.raises(InvoicingError):
        create_reminder(db_path, doc.id, render_document, today=due + timedelta(days=25))
    set_reminder_block(db_path, doc.id, "")
    record_payment(db_path, doc.id, "0.10", due + timedelta(days=21))
    assert reminder_proposals(db_path, today=due + timedelta(days=25)) == []


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
    with pytest.raises(Redirect):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="", segments=["issue"],
                                                 form={"csrf_token": csrf, "run_id": str(run_id)}))
    for tab in ("invoices", "credits", "reminders", "settings"):
        html = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", query={"tab": tab}))
        assert "Rechnungen &amp; Gutschriften" in html
    html = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", query={"tab": "invoices"}))
    assert "Consumer A" in html and "Freigegebene Abrechnungen ohne Belege" not in html

    docs = _by_participant(db_path)
    detail = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[str(docs["cons_a"].id)]))
    assert "Zahlung erfassen" in detail and "Mahnwesen" in detail
    credit = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[str(docs["solar"].id)]))
    assert "Auszahlung erfassen" in credit and "Jetzt auszahlbar" in credit
    printed = invoices_page.handle_get(RequestContext(method="GET", ingress_path="",
                                                      segments=[str(docs["cons_a"].id), "print"]))
    assert docs["cons_a"].snapshot_sha256 in printed

    with pytest.raises(Redirect):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="",
                                                 segments=[str(docs["cons_a"].id), "payment"],
                                                 form={"csrf_token": csrf, "amount": "0.10", "booked_on": "2027-08-10"}))
    with pytest.raises(FormError, match="Ziff. 3.4"):
        invoices_page.handle_post(RequestContext(method="POST", ingress_path="",
                                                 segments=[str(docs["solar"].id), "payout"],
                                                 form={"csrf_token": csrf, "amount": "0.20", "reference": "X"}))
    assert get_participant(db_path, "cons_a").label == "Consumer A"
