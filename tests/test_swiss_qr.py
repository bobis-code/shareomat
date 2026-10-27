# -*- coding: utf-8 -*-
"""Swiss QR bill (shareomat.core.payments.swiss_qr) and its integration into invoices and reminders."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
import segno

from shareomat.core.payments.swiss_qr import (
    QrAddress,
    SwissQrError,
    SwissQrPayload,
    creditor_reference,
    format_reference,
    is_qr_iban,
    qr_reference,
    qr_svg,
    reference_for,
    reference_is_valid,
    split_street,
)
from shareomat.database.invoicing import (
    InvoicingError,
    cancel_document,
    confirm_payment_profile,
    create_reminder,
    get_document,
    get_payment_profile,
    issue_documents,
    issue_problems,
    payment_profile_status,
    record_delivery,
    record_payment,
    save_payment_profile,
)
from shareomat.models.invoicing import PaymentProfile
from shareomat.web.pages.invoices import render_document
from tests.test_invoicing import (  # noqa: F401  (fixtures)
    IBAN,
    ISSUED,
    LATER,
    _SIMPLE,
    _by_participant,
    _ready_profile,
    _released_run,
    db_path,
    runtime,
)

QR_IBAN = "CH44 3199 9123 0008 8901 2"          # SIX sample QR-IBAN
CREDITOR = QrAddress(name="LEG Test Konto", street="Dorfstrasse", building="1", postal_code="4000", city="Basel")
DEBTOR = QrAddress(name="Consumer A", street="Weg", building="1", postal_code="4000", city="Basel")


def _payload(**kw) -> SwissQrPayload:
    args = dict(iban=IBAN, creditor=CREDITOR, amount=Decimal("10.56"), debtor=DEBTOR, reference_type="SCOR",
                reference=creditor_reference("R-2026-001"), message="Rechnung R-2026-001")
    args.update(kw)
    return SwissQrPayload.build(**args)


# ── references ──────────────────────────────────────────────────────────────


def test_reference_check_digits_match_the_standard_examples():
    assert creditor_reference("539007547034") == "RF18539007547034"           # ISO 11649 example
    assert qr_reference("21000000000313947143000901") == "210000000003139471430009017"   # SIX example
    assert reference_is_valid("SCOR", "RF18539007547034") and not reference_is_valid("SCOR", "RF19539007547034")
    assert reference_is_valid("QRR", "210000000003139471430009017")
    assert not reference_is_valid("QRR", "210000000003139471430009018")


def test_reference_type_follows_the_account():
    assert is_qr_iban(QR_IBAN) and not is_qr_iban(IBAN)
    assert reference_for(IBAN, "R-2026-001") == ("SCOR", "RF43R2026001")
    assert reference_for(QR_IBAN, "R-2026-001")[0] == "QRR"
    _payload(iban=QR_IBAN, reference_type="QRR", reference=qr_reference("R-2026-001"))
    with pytest.raises(SwissQrError, match="nur mit einer QR-IBAN"):
        _payload(reference_type="QRR", reference=qr_reference("R-2026-001"))
    with pytest.raises(SwissQrError, match="verlangt eine QR-Referenz"):
        _payload(iban=QR_IBAN)
    with pytest.raises(SwissQrError, match="verlangt eine QR-Referenz"):
        _payload(iban=QR_IBAN, reference_type="NON", reference="")
    _payload(reference_type="NON", reference="")


def test_two_documents_get_different_stable_references():
    assert creditor_reference("R-2026-001") != creditor_reference("R-2026-002")
    assert creditor_reference("R-2026-001") == creditor_reference("R-2026-001")


# ── payload validation ──────────────────────────────────────────────────────


def test_payload_follows_the_spc_structure():
    lines = _payload().text().split("\r\n")
    assert len(lines) == 31
    assert lines[:4] == ["SPC", "0200", "1", "CH9300762011623852957"]
    assert lines[4:11] == ["S", "LEG Test Konto", "Dorfstrasse", "1", "4000", "Basel", "CH"]
    assert lines[11:18] == [""] * 7                                       # ultimate creditor (reserved)
    assert lines[18:20] == ["10.56", "CHF"]
    assert lines[20:27] == ["S", "Consumer A", "Weg", "1", "4000", "Basel", "CH"]
    assert lines[27:] == ["SCOR", "RF43R2026001", "Rechnung R-2026-001", "EPD"]


@pytest.mark.parametrize("change, message", [
    (dict(iban="CH93 0076 2011 6238 5295 8"), "IBAN ungültig"),
    (dict(iban="DE89 3704 0044 0532 0130 00"), "IBAN ungültig"),
    (dict(creditor=QrAddress(name="", postal_code="4000", city="Basel")), "Name fehlt"),
    (dict(creditor=QrAddress(name="X", city="Basel")), "PLZ fehlt"),
    (dict(creditor=QrAddress(name="X", postal_code="4000")), "Ort fehlt"),
    (dict(creditor=QrAddress(name="X", postal_code="4000", city="Basel", country="")), "Land"),
    (dict(creditor=QrAddress(name="X中", postal_code="4000", city="Basel")), "unzulässige Zeichen"),
    (dict(amount=Decimal("0.00")), "Betrag"),
    (dict(amount=Decimal("1.005")), "Betrag"),
    (dict(reference="RF00R2026001"), "ungültig"),
    (dict(message="x" * 141), "140 Zeichen"),
])
def test_invalid_data_is_refused_not_cleaned(change, message):
    with pytest.raises(SwissQrError, match=message):
        _payload(**change)


def test_qr_code_is_level_m_with_swiss_cross_and_46mm():
    text = _payload().text()
    assert segno.make(text, error="m", boost_error=False).error == "M"
    svg = qr_svg(text)
    assert 'width="46mm" height="46mm"' in svg and svg.count("<rect") == 5     # background + cross (4)
    assert qr_svg(text) == svg                                                  # deterministic reprint


def test_street_and_house_number_are_split_for_type_s():
    assert split_street("Hinterholzring 24") == ("Hinterholzring", "24")
    assert split_street("Im Bifang 3b") == ("Im Bifang", "3b")
    assert split_street("Postfach") == ("Postfach", "")


# ── payment profile ─────────────────────────────────────────────────────────


def test_profile_status_missing_check_ready_and_reset_on_change(db_path):
    assert payment_profile_status(db_path) == "missing"
    with pytest.raises(InvoicingError, match="Bankkontoinhaber fehlt"):
        save_payment_profile(db_path, PaymentProfile(iban=IBAN, postal_code="4000", city="Basel"))
    with pytest.raises(InvoicingError, match="PLZ fehlt"):
        save_payment_profile(db_path, PaymentProfile(payee="LEG", iban=IBAN, city="Basel"))
    save_payment_profile(db_path, PaymentProfile(payee="LEG", street="Weg", building="1", postal_code="4000",
                                                 city="Basel", iban=IBAN))
    assert payment_profile_status(db_path) == "check"
    with pytest.raises(InvoicingError, match="geändert"):
        confirm_payment_profile(db_path, version="0" * 16)
    confirm_payment_profile(db_path, version=get_payment_profile(db_path).version)
    assert payment_profile_status(db_path) == "ready"
    save_payment_profile(db_path, PaymentProfile(payee="LEG", street="Weg", building="1", postal_code="4000",
                                                 city="Basel", iban=IBAN, contact_email="neu@example.ch"))
    assert payment_profile_status(db_path) == "ready"            # contact e-mail is not part of the QR bill
    save_payment_profile(db_path, PaymentProfile(payee="LEG Konto", street="Weg", building="1", postal_code="4000",
                                                 city="Basel", iban=IBAN))
    assert payment_profile_status(db_path) == "check"            # holder changed -> confirm again


def test_invoices_are_blocked_until_the_profile_is_confirmed(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    assert any("Bankdaten fehlen" in p for p in issue_problems(db_path, run_id))
    save_payment_profile(db_path, PaymentProfile(payee="LEG", street="Weg", building="1", postal_code="4000",
                                                 city="Basel", iban=IBAN, contact_email="leg@example.ch"))
    with pytest.raises(InvoicingError, match="noch nicht bestätigt"):
        issue_documents(db_path, run_id, render_document, today=ISSUED)
    confirm_payment_profile(db_path, version=get_payment_profile(db_path).version)
    assert issue_documents(db_path, run_id, render_document, today=ISSUED)


# ── documents ───────────────────────────────────────────────────────────────


def test_invoice_carries_a_frozen_valid_qr_bill(db_path, runtime, issued_qr):
    docs = _by_participant(db_path)
    invoice, credit = docs["cons_a"], docs["solar"]
    assert invoice.qr_reference_type == "SCOR" and invoice.qr_reference == creditor_reference(invoice.number)
    lines = invoice.qr_payload.split("\r\n")
    assert lines[18:20] == ["0.10", "CHF"] and lines[21] == "Consumer A" and lines[28] == invoice.qr_reference
    assert invoice.qr_iban_snapshot == "CH9300762011623852957" and invoice.qr_account_holder_snapshot == "LEG Test Konto"
    assert qr_svg(invoice.qr_payload) in invoice.snapshot_html                  # reprint = identical code
    assert "Empfangsschein" in invoice.snapshot_html and format_reference(invoice.qr_reference) in invoice.snapshot_html
    assert "Kontakt: leg@example.ch" in invoice.snapshot_html
    assert credit.qr_payload == "" and "<svg" not in credit.snapshot_html     # credit notes: no payment QR
    assert docs["cons_a"].qr_reference != docs["cons_b"].qr_reference


def test_prosumer_qr_carries_the_balance(db_path, runtime):
    run_id = _released_run(db_path, runtime, {("M3", "import"): "2.0", ("M3", "export"): "0.5",
                                              ("M1", "export"): "1.5"})
    _ready_profile(db_path)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    doc = _by_participant(db_path)["prosumer"]
    assert doc.doc_type == "invoice"
    assert doc.qr_payload.split("\r\n")[18] == f"{doc.amount_chf:.2f}" == "0.15"   # 2.0 x 0.10 - 0.5 x 0.10


def test_profile_change_after_issuing_leaves_documents_untouched(db_path, runtime, issued_qr):
    doc = _by_participant(db_path)["cons_a"]
    save_payment_profile(db_path, PaymentProfile(payee="Anderes Konto", street="Neu", building="2",
                                                 postal_code="4410", city="Liestal", iban=QR_IBAN))
    again = get_document(db_path, doc.id)
    assert (again.snapshot_html, again.qr_payload, again.qr_reference) == (doc.snapshot_html, doc.qr_payload,
                                                                           doc.qr_reference)
    assert again.integrity_ok


def test_reminder_uses_the_same_reference_and_the_open_amount(db_path, runtime, issued_qr):
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    record_payment(db_path, doc.id, "0.04", ISSUED, reference="teil", today=LATER)
    reminder = create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=5))
    lines = reminder.qr_payload.split("\r\n")
    assert reminder.qr_reference == doc.qr_reference == lines[28]
    assert lines[18] == "0.06" and qr_svg(reminder.qr_payload) in reminder.snapshot_html


def test_reminder_refuses_an_account_with_another_reference_type(db_path, runtime, issued_qr):
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    save_payment_profile(db_path, PaymentProfile(payee="LEG Test Konto", street="Dorfstrasse", building="1",
                                                 postal_code="4000", city="Basel", iban=QR_IBAN))
    confirm_payment_profile(db_path, version=get_payment_profile(db_path).version)
    with pytest.raises(InvoicingError, match="passt nicht zum aktuellen Konto"):
        create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=5))


def test_replacement_gets_a_new_reference(db_path, runtime, issued_qr):
    old = _by_participant(db_path)["cons_b"]
    cancel_document(db_path, old.id, "Adresse", render_document)
    new = issue_documents(db_path, issued_qr, render_document, today=ISSUED)[0]
    assert new.qr_reference != old.qr_reference
    assert get_document(db_path, old.id).qr_reference == old.qr_reference      # history unchanged


@pytest.fixture
def issued_qr(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    _ready_profile(db_path)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    return run_id
