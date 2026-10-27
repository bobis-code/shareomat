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


# ── audit round 4 (71267d3) ─────────────────────────────────────────────────


def test_tampered_qr_reference_fails_integrity_and_blocks_reminders(db_path, runtime, issued_qr):
    """P1-01 reproduced: only billing_documents.qr_reference changed -> integrity fails, no reminder."""
    from shareomat.database.sqlite import connect

    docs = _by_participant(db_path)
    a, b = docs["cons_a"], docs["cons_b"]
    record_delivery(db_path, a.id, "paper")
    for column, value in (("qr_reference", b.qr_reference), ("qr_iban_snapshot", "CH4431999123000889012"),
                          ("amount_chf", "9.99"), ("qr_payload", b.qr_payload)):
        with connect(db_path) as conn:
            with conn:
                original = conn.execute(f"SELECT {column} FROM billing_documents WHERE id = ?", (a.id,)).fetchone()[0]
                conn.execute(f"UPDATE billing_documents SET {column} = ? WHERE id = ?", (value, a.id))
        assert not get_document(db_path, a.id).integrity_ok, column
        with pytest.raises(InvoicingError, match="Prüfsumme"):
            create_reminder(db_path, a.id, render_document, today=a.due_date + timedelta(days=5))
        with connect(db_path) as conn:
            with conn:
                conn.execute(f"UPDATE billing_documents SET {column} = ? WHERE id = ?", (original, a.id))
        assert get_document(db_path, a.id).integrity_ok
    reminder = create_reminder(db_path, a.id, render_document, today=a.due_date + timedelta(days=5))
    assert reminder.qr_reference == a.qr_reference and reminder.integrity_ok


def test_tampered_reminder_record_fails_integrity(db_path, runtime, issued_qr):
    from shareomat.database.sqlite import connect

    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    reminder = create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=5))
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE document_reminders SET open_amount_chf = '0.01' WHERE id = ?", (reminder.id,))
    assert not get_document(db_path, doc.id).integrity_ok


def test_data_that_would_overflow_the_payment_part_is_refused():
    """P1-03: allowed field lengths alone don't guarantee a legible payment part -> hard stop, no cutting."""
    long_creditor = QrAddress(name="A" * 70, street="B" * 70, building="1" * 16, postal_code="4000", city="C" * 35)
    long_debtor = QrAddress(name="Kunde " + "Langer" * 10, street="Testgasse", building="2", postal_code="4000",
                            city="Musterdorf")
    with pytest.raises(SwissQrError, match="zu lang"):
        _payload(creditor=long_creditor, debtor=long_debtor, message="x" * 140)
    _payload(debtor=long_debtor)                    # a long debtor name alone still fits (wrapped)


def test_issuing_is_blocked_when_the_payment_part_would_overflow(db_path, runtime):
    run_id = _released_run(db_path, runtime, _SIMPLE)
    save_payment_profile(db_path, PaymentProfile(payee="A" * 70, street="B" * 70, building="1" * 16,
                                                 postal_code="4000", city="C" * 35, iban=IBAN))
    confirm_payment_profile(db_path, version=get_payment_profile(db_path).version)
    with pytest.raises(InvoicingError, match="zu lang"):
        issue_documents(db_path, run_id, render_document, today=ISSUED)


def test_qr_iban_only_in_chf():
    """P2-01: QR-IBAN/QRR is CHF only (IG 2.4)."""
    with pytest.raises(SwissQrError, match="nur in CHF"):
        _payload(iban=QR_IBAN, reference_type="QRR", reference=qr_reference("R-2026-001"), currency="EUR")
    _payload(currency="EUR")


def test_country_must_be_an_iso_code():
    """P2-02: ZZ is not an ISO 3166-1 code."""
    with pytest.raises(SwissQrError, match="ISO-3166"):
        _payload(creditor=QrAddress(name="X", postal_code="4000", city="Basel", country="ZZ"))
    _payload(creditor=QrAddress(name="X", postal_code="9490", city="Vaduz", country="LI"))


def test_sample_without_debtor_has_the_writing_field_with_corner_marks(db_path):
    """P2-03: empty debtor -> 'Zahlbar durch (Name/Adresse)' with corner marks on receipt and payment part."""
    from shareomat.database.invoicing import sample_payment_part
    from shareomat.web.rendering import _env

    _ready_profile(db_path)
    html = _env.get_template("invoices/qr_payment_part.html").render(qr=sample_payment_part(db_path))
    assert html.count("Zahlbar durch (Name/Adresse)") == 2
    assert 'width="52mm" height="20mm"' in html and 'width="65mm" height="25mm"' in html


def _browser() -> str | None:
    import shutil
    from pathlib import Path

    for candidate in ("msedge", "chrome", "chromium", "google-chrome",
                      r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                      r"C:\Program Files\Google\Chrome\Application\chrome.exe"):
        found = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if found:
            return found
    return None


@pytest.mark.skipif(_browser() is None, reason="needs Edge/Chrome for the print geometry test")
def test_printed_payment_part_sits_at_the_bottom_of_a4(db_path, runtime, issued_qr, tmp_path):
    """P1-02 / P2-04: real A4 print (headless browser): one page, payment part 105 mm at the bottom edge,
    QR 46 x 46 mm at x 67 / y 209 mm, and — when OpenCV is available — the printed code decodes to the payload."""
    import subprocess

    fitz = pytest.importorskip("fitz")
    from shareomat.web.pages import invoices as invoices_page
    from shareomat.web.rendering import RequestContext

    doc = _by_participant(db_path)["cons_a"]
    html = invoices_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[str(doc.id), "print"]))
    page_file, pdf_file = tmp_path / "invoice.html", tmp_path / "invoice.pdf"
    page_file.write_text(html, encoding="utf-8")
    subprocess.run([_browser(), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={pdf_file}", page_file.as_uri()], check=True, timeout=120,
                   capture_output=True)
    pdf = fitz.open(pdf_file)
    mm = 72 / 25.4
    assert len(pdf) == 1
    page = pdf[0]
    assert abs(page.rect.height / mm - 297) < 0.5
    receipt = page.search_for("Empfangsschein")[0]
    assert abs(receipt.y0 / mm - 197.3) < 1.0                   # 192 mm separator + 5 mm margin
    qr_boxes = [d["rect"] for d in page.get_drawings()
                if abs(d["rect"].width / mm - 46) < 0.3 and abs(d["rect"].height / mm - 46) < 0.3]
    assert qr_boxes and abs(qr_boxes[0].x0 / mm - 67) < 0.5 and abs(qr_boxes[0].y0 / mm - 209) < 0.8
    sha = page.search_for("SHA-256")[0]
    assert sha.y1 / mm < 192                                    # nothing of the letter below the separator
    try:
        import cv2
        import numpy as np
    except ImportError:
        return
    pix = page.get_pixmap(dpi=300)
    image = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3]
    text, *_ = cv2.QRCodeDetectorAruco().detectAndDecode(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY))
    assert text == doc.qr_payload
