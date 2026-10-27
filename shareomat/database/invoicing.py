# -*- coding: utf-8 -*-
"""
File: shareomat/database/invoicing.py

Purpose:
    "Rechnungen & Gutschriften": issue one document per participant from a
    released billing run, and keep the append-only logs of payments
    received, refunds, producer payouts, deliveries and payment reminders.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Contract §3.4 (Mustervertrag + decision of 27.10.2026):
      - Prosumers get ONE document with the net balance: invoice when the
        charge exceeds the credit, credit note otherwise; a zero balance
        gets no document.
      - Credits are paid out only from, and as far as, consumer payments for
        the same billing run arrived. Each producer's share is
        floor(received x credit / all credits) to the cent — monotone in the
        received amount, so a later payment can never lower a share that was
        already paid out; the last cents follow once everything was paid.
        Every payout is checked against both the producer's share and the
        run total (sum of payouts <= received) — no pre-financing.

    Integrity:
      - Every check-then-write runs in ONE write transaction (BEGIN
        IMMEDIATE), so two concurrent clicks can't both pass a check.
      - Documents are rendered once and frozen with SHA-256; recipients come
        from the snapshot taken when the billing draft was saved, never from
        the current master data.
      - Only runs released WITHOUT an override (all quality checks passed)
        can be invoiced; an overridden run stays an internal evaluation.
      - A delivered document can only be cancelled with a numbered
        cancellation notice (Stornobeleg) that must be delivered before a
        replacement is issued; an undelivered one is simply cancelled.
        Nothing booked on it is a precondition either way.
      - A payment reference already used needs an explicit confirmation
        (double booking of one bank transfer); a refund reference can never
        be reused.
      - A correction lowering received payments is refused when any producer
        was already paid more than the share recomputed after it.
      - Issuing checks everything inside its write transaction and only
        stores exactly the documents the user approved (fingerprint of the
        preview); a document whose checksum fails is frozen for every
        further action.

    Rendering is injected (`render(template_name, context) -> html`) so this
    module stays free of the web layer.

    Not yet here (later phases): e-mail sending via SMTP, Swiss QR bill,
    camt.054 bank import, pain.001 payout files, correction documents after
    payments or for corrected grid-operator data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.core.payments.swiss_qr import (
    QR_RENDER_VERSION,
    PaymentPartView,
    QrAddress,
    SwissQrError,
    SwissQrPayload,
    format_iban,
    iban_is_valid,
    normalize_iban,
    reference_for,
    reference_type_for,
    split_street,
)
from shareomat.database.audit import log_event, record_event
from shareomat.database.billing import get_billing_run
from shareomat.database.community import get_community
from shareomat.database.contract_versions import get_contract_version_for_date, list_contract_versions
from shareomat.database.settings import get_setting, set_setting
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.leg_const import BILLING_STATUS_RELEASED, CONTRACT_STATUS_PUBLISHED
from shareomat.models.invoicing import (
    DELIVERY_ITEMS,
    DELIVERY_METHODS,
    DOC_STATUS_CANCELLED,
    DOC_STATUS_ISSUED,
    DOC_TYPE_CREDIT,
    DOC_TYPE_INVOICE,
    PAYMENT_KIND_PAYMENT,
    PAYMENT_KIND_PAYOUT,
    PAYMENT_KIND_REFUND,
    REMINDER_LABELS,
    REMINDER_OFFSETS_DAYS,
    PROFILE_CHECK,
    PROFILE_MISSING,
    PROFILE_READY,
    BillingDocument,
    DocumentDelivery,
    DocumentPayment,
    DocumentReminder,
    PaymentProfile,
    PayoutStatus,
    ReminderProposal,
)

logger = logging.getLogger(__name__)

_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
_CENT = Decimal("0.01")

Renderer = Callable[[str, dict], str]

SETTING_PROFILE = "invoice_payment_profile"
SETTING_PROFILE_CONFIRMED = "invoice_payment_profile_confirmed"   # profile version the user confirmed

REMINDER_PAYMENT_DAYS = 10    # new payment deadline printed on a reminder
REMINDER_GRACE_DAYS = 5       # after that deadline before the next level is proposed


class InvoicingError(Exception):
    """A document action was refused; the message is shown to the user."""


def local_today() -> date:
    return datetime.now(_TZ).date()


@contextmanager
def _write_txn(db_path: Path) -> Iterator[sqlite3.Connection]:
    """One write transaction taken BEFORE the checks read anything (serializes concurrent writers)."""
    with connect(db_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _require_integrity(doc: BillingDocument) -> None:
    if not doc.integrity_ok:
        raise InvoicingError(f"Prüfsumme von {doc.number} stimmt nicht — der gespeicherte Beleg wurde verändert. "
                             "Keine weiteren Schritte, bis er aus der Sicherung wiederhergestellt ist.")


# ── payment details (Zahlungsangaben) ───────────────────────────────────────


def get_payment_profile(db_path: Path) -> PaymentProfile:
    """The stored payment profile (empty when not set up)."""
    raw = get_setting(db_path, SETTING_PROFILE)
    return PaymentProfile(**json.loads(raw)) if raw else PaymentProfile()


def _profile_creditor(profile: PaymentProfile) -> QrAddress:
    return QrAddress(name=profile.payee, street=profile.street, building=profile.building,
                     postal_code=profile.postal_code, city=profile.city, country=profile.country)


def validate_payment_profile(profile: PaymentProfile) -> PaymentProfile:
    """Server-side validation; returns the normalized profile or raises InvoicingError naming the field."""
    if not profile.payee.strip():
        raise InvoicingError("Bankkontoinhaber fehlt — genau so, wie ihn die Bank für das Konto führt.")
    iban = normalize_iban(profile.iban)
    if not iban_is_valid(iban):
        raise InvoicingError("IBAN ungültig: Schweizer oder Liechtensteiner IBAN / QR-IBAN, 21 Zeichen, Prüfziffer.")
    try:
        address = _profile_creditor(profile).validated("Zahlungsempfänger")
    except SwissQrError as exc:
        raise InvoicingError(str(exc))
    email = profile.contact_email.strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise InvoicingError("Kontakt-E-Mail ist ungültig.")
    return PaymentProfile(payee=address.name, street=address.street, building=address.building,
                          postal_code=address.postal_code, city=address.city, country=address.country,
                          iban=iban, contact_email=email)


def save_payment_profile(db_path: Path, profile: PaymentProfile) -> PaymentProfile:
    """Store a validated profile. A changed account/payee/address needs a new confirmation (status "prüfen")."""
    validated = validate_payment_profile(profile)
    previous = get_payment_profile(db_path)
    set_setting(db_path, SETTING_PROFILE, json.dumps(validated.as_dict(), ensure_ascii=False))
    reset = previous.iban and previous.version != validated.version
    log_event(db_path, "payment_profile", "invoice", "saved",
              f"Kontoinhaber {validated.payee}, IBAN …{validated.iban[-4:]}, Version {validated.version}"
              + (" — Bestätigung zurückgesetzt" if reset else ""))
    return validated


def payment_profile_status(db_path: Path) -> str:
    """PROFILE_MISSING (nicht eingerichtet) / PROFILE_CHECK (prüfen) / PROFILE_READY (bereit)."""
    profile = get_payment_profile(db_path)
    if not profile.iban:
        return PROFILE_MISSING
    try:
        validate_payment_profile(profile)
    except InvoicingError:
        return PROFILE_MISSING
    return PROFILE_READY if get_setting(db_path, SETTING_PROFILE_CONFIRMED) == profile.version else PROFILE_CHECK


def confirm_payment_profile(db_path: Path, *, version: str) -> None:
    """The user confirms: account holder/address matched with the bank, sample payment part passed the SIX
    validation portal and a banking-app scan. Bound to the profile version that was shown."""
    profile = get_payment_profile(db_path)
    if payment_profile_status(db_path) == PROFILE_MISSING:
        raise InvoicingError("Zuerst gültige Zahlungsangaben speichern.")
    if version != profile.version:
        raise InvoicingError("Die Zahlungsangaben haben sich seit dem angezeigten Muster geändert — bitte neu prüfen.")
    set_setting(db_path, SETTING_PROFILE_CONFIRMED, profile.version)
    log_event(db_path, "payment_profile", "invoice", "confirmed",
              f"Scan-Test und Bankabgleich bestätigt, Version {profile.version}")


def payment_problems(db_path: Path) -> list[str]:
    """Why invoices with a QR payment part can't be issued now (empty = profile ready)."""
    status = payment_profile_status(db_path)
    if status == PROFILE_MISSING:
        return ["Bankdaten fehlen – nicht versandbereit: Kontoinhaber, Adresse und IBAN unter "
                "Rechnungen › Zahlungsangaben erfassen."]
    if status == PROFILE_CHECK:
        return ["Zahlungsangaben noch nicht bestätigt: Musterzahlteil mit dem SIX-Validierungsportal und einer "
                "Banking-App prüfen, dann unter Rechnungen › Zahlungsangaben bestätigen."]
    return []


def sample_payment_part(db_path: Path) -> PaymentPartView | None:
    """Sample payment part for the one-time scan test (CHF 1.00, test reference). None without valid data."""
    if payment_profile_status(db_path) == PROFILE_MISSING:
        return None
    profile = get_payment_profile(db_path)
    ref_type, reference = reference_for(profile.iban, "TEST0001")
    return PaymentPartView(SwissQrPayload.build(
        iban=profile.iban, creditor=_profile_creditor(profile), amount=Decimal("1.00"), debtor=None,
        reference_type=ref_type, reference=reference, message="Scan-Test Shareomat - bitte nicht bezahlen"))


def _debtor(label: str, address_line: str, postal_code: str, city: str) -> QrAddress:
    street, building = split_street(address_line)
    return QrAddress(name=label, street=street, building=building, postal_code=postal_code, city=city, country="CH")


def _debtor_from_document(doc: BillingDocument) -> QrAddress:
    lines = doc.recipient_address.split("\n")
    street = lines[0] if len(lines) > 1 else ""
    postal_code, _, city = (lines[-1] if lines else "").partition(" ")
    return _debtor(doc.recipient_label, street, postal_code, city)


def _qr_message(number: str, period_start: date | None, period_end: date | None) -> str:
    if period_start and period_end:
        return f"Rechnung {number}, LEG-Strom {period_start:%d.%m.%Y}-{period_end:%d.%m.%Y}"
    return f"Rechnung {number}"


# ── reading ─────────────────────────────────────────────────────────────────

_DOC_SELECT = """
    SELECT d.*, bp.period_start AS bp_start, bp.period_end AS bp_end,
           r.local_received_kwh AS rec_received, r.local_supplied_kwh AS rec_supplied,
           rep.number AS replaces_number
    FROM billing_documents d
    JOIN billing_runs br ON br.id = d.billing_run_id
    JOIN billing_periods bp ON bp.id = br.billing_period_id
    LEFT JOIN billing_records r ON r.billing_run_id = d.billing_run_id AND r.participant_id = d.participant_id
    LEFT JOIN billing_documents rep ON rep.id = d.replaces_document_id
"""


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _load_logs(conn, doc: BillingDocument) -> None:
    doc.payments = [
        DocumentPayment(id=r["id"], document_id=r["document_id"], kind=r["kind"], amount_chf=Decimal(r["amount_chf"]),
                        booked_on=str_to_date(r["booked_on"]), reference=r["reference"], note=r["note"],
                        recorded_at=_ts(r["recorded_at"]))
        for r in conn.execute("SELECT * FROM document_payments WHERE document_id = ? ORDER BY booked_on, id", (doc.id,))
    ]
    doc.booked_chf = sum((p.amount_chf for p in doc.payments), Decimal(0))
    doc.deliveries = [
        DocumentDelivery(id=r["id"], document_id=r["document_id"], reminder_id=r["reminder_id"], method=r["method"],
                         status=r["status"], detail=r["detail"], item=r["item"], recorded_at=_ts(r["recorded_at"]))
        for r in conn.execute("SELECT * FROM document_deliveries WHERE document_id = ? ORDER BY id", (doc.id,))
    ]

    def first_sent(item: str, reminder_id: int | None = None) -> datetime | None:
        sent = [d.recorded_at for d in doc.deliveries
                if d.status == "sent" and d.item == item and d.reminder_id == reminder_id]
        return sent[0] if sent else None

    doc.sent_at = first_sent("document")
    doc.cancellation_sent_at = first_sent("cancellation")
    doc.reminders = [
        DocumentReminder(
            id=r["id"], document_id=r["document_id"], level=r["level"], issue_date=str_to_date(r["issue_date"]),
            open_amount_chf=Decimal(r["open_amount_chf"]), snapshot_html=r["snapshot_html"],
            snapshot_sha256=r["snapshot_sha256"], new_due_date=str_to_date(r["new_due_date"]),
            discarded_at=_ts(r["discarded_at"]), sent_at=first_sent("reminder", r["id"]),
            qr_payload=r["qr_payload"], qr_reference=r["qr_reference"])
        for r in conn.execute("SELECT * FROM document_reminders WHERE document_id = ? ORDER BY id", (doc.id,))
    ]


def _row_to_document(row) -> BillingDocument:
    is_invoice = row["doc_type"] == DOC_TYPE_INVOICE
    kwh = row["rec_received"] if is_invoice else row["rec_supplied"]
    return BillingDocument(
        id=row["id"], billing_run_id=row["billing_run_id"], participant_id=row["participant_id"],
        doc_type=row["doc_type"], number=row["number"], status=row["status"], amount_chf=Decimal(row["amount_chf"]),
        issue_date=str_to_date(row["issue_date"]), due_date=str_to_date(row["due_date"]),
        recipient_label=row["recipient_label"], recipient_email=row["recipient_email"],
        recipient_address=row["recipient_address"],
        snapshot_html=row["snapshot_html"], snapshot_sha256=row["snapshot_sha256"],
        reminder_block_reason=row["reminder_block_reason"], cancel_reason=row["cancel_reason"],
        cancelled_at=_ts(row["cancelled_at"]), cancel_number=row["cancel_number"] or "",
        cancel_snapshot_html=row["cancel_snapshot_html"], cancel_sha256=row["cancel_sha256"],
        replaces_number=row["replaces_number"] or "", contract_versions=row["contract_versions"],
        qr_payload=row["qr_payload"], qr_reference=row["qr_reference"], qr_reference_type=row["qr_reference_type"],
        qr_iban_snapshot=row["qr_iban_snapshot"], qr_account_holder_snapshot=row["qr_account_holder_snapshot"],
        period_start=str_to_date(row["bp_start"]), period_end=str_to_date(row["bp_end"]),
        local_kwh=Decimal(kwh or "0"),
    )


def _load_document(conn, document_id: int) -> BillingDocument:
    row = conn.execute(_DOC_SELECT + " WHERE d.id = ?", (document_id,)).fetchone()
    if row is None:
        raise InvoicingError(f"Beleg {document_id} wurde nicht gefunden.")
    doc = _row_to_document(row)
    _load_logs(conn, doc)
    return doc


def _query_documents(conn, where: list[str], params: list) -> list[BillingDocument]:
    sql = _DOC_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY d.issue_date DESC, d.number DESC"
    docs = [_row_to_document(r) for r in conn.execute(sql, params).fetchall()]
    for doc in docs:
        _load_logs(conn, doc)
    return docs


def list_documents(db_path: Path, *, doc_type: str | None = None, run_id: int | None = None) -> list[BillingDocument]:
    """All documents, newest first, with their logs loaded."""
    where, params = [], []
    if doc_type:
        where.append("d.doc_type = ?")
        params.append(doc_type)
    if run_id is not None:
        where.append("d.billing_run_id = ?")
        params.append(run_id)
    with connect(db_path) as conn:
        return _query_documents(conn, where, params)


def get_document(db_path: Path, document_id: int) -> BillingDocument:
    with connect(db_path) as conn:
        return _load_document(conn, document_id)


# ── issuing ─────────────────────────────────────────────────────────────────


def _next_number(conn, prefix: str, year: int) -> str:
    """Next free number; called inside the write transaction, so two issuers can't take the same one."""
    stem = f"{prefix}-{year}-"
    used = []
    for column in ("number", "cancel_number"):
        for r in conn.execute(f"SELECT {column} AS n FROM billing_documents WHERE {column} LIKE ?", (stem + "%",)):
            tail = r["n"][len(stem):]
            if tail.isdigit():
                used.append(int(tail))
    return f"{stem}{(max(used) + 1 if used else 1):03d}"


def _recipients(conn, run_id: int) -> dict[str, dict]:
    """Recipients as frozen when the billing draft was saved (participant_id -> label/address/e-mail)."""
    result = {}
    for r in conn.execute("SELECT participant_id, recipient_snapshot FROM billing_records WHERE billing_run_id = ?",
                          (run_id,)):
        result[r["participant_id"]] = json.loads(r["recipient_snapshot"]) if r["recipient_snapshot"] else {}
    return result


def _billable(item) -> bool:
    """Participants with a non-zero balance get a document (zero balance: nothing to pay or pay out)."""
    return item.net_amount_chf != 0


def _creditor(db_path: Path, issue_date: date, period_start: date | None, period_end: date | None) -> dict:
    """Issuer as of the document date (contract in force then, else at period end), and the contract
    versions in force during the period."""
    version = get_contract_version_for_date(db_path, issue_date) or (
        get_contract_version_for_date(db_path, period_end) if period_end else None)
    in_period = []
    if period_start and period_end:
        for v in sorted(list_contract_versions(db_path), key=lambda v: v.version):
            if v.status != CONTRACT_STATUS_PUBLISHED or v.valid_from is None or v.valid_from > period_end:
                continue
            if v.valid_until is not None and v.valid_until < period_start:
                continue
            in_period.append(v.version)
    versions = ", ".join(str(v) for v in in_period)
    if version is not None and version.representative_name:
        return {"name": version.representative_name, "address_line": version.representative_address_line,
                "postal_code": version.representative_postal_code, "city": version.representative_city,
                "email": version.representative_email, "contract_versions": versions,
                "payment_term_days": version.payment_term_days}
    community = get_community(db_path)
    return {"name": community.name if community else "", "address_line": community.address_line if community else "",
            "postal_code": community.postal_code if community else "", "city": community.city if community else "",
            "email": "", "contract_versions": versions, "payment_term_days": 30}


def issue_problems(db_path: Path, run_id: int, *, today: date | None = None, include_payment: bool = True) -> list[str]:
    """Reasons the documents of a run can't be issued yet (empty = ready).

    include_payment=False leaves out the payment profile (the approval view
    shows documents without bank data, marked "nicht versandbereit").
    """
    today = today or local_today()
    detail = get_billing_run(db_path, run_id)
    run = detail.run
    problems: list[str] = []
    if run.status != BILLING_STATUS_RELEASED:
        problems.append("Nur freigegebene Abrechnungen können verrechnet werden.")
    if run.release_override_reason:
        problems.append("Diese Abrechnung wurde trotz offener Prüfpunkte (mit Begründung) freigegeben — sie gilt nur "
                        "als interne Auswertung. Rechnungen nur aus vollständig geprüften Abrechnungen: Daten "
                        "ergänzen, neu berechnen und ohne Übersteuerung freigeben.")
    if include_payment and any(i.net_amount_chf > 0 for i in detail.line_items):
        problems.extend(payment_problems(db_path))
    creditor = _creditor(db_path, today, run.period_start, run.period_end)
    if not creditor["name"] or not creditor["address_line"] or not creditor["city"]:
        problems.append("Name und Adresse des LEG-Vertreters fehlen (Vertrag bzw. Gemeinschaft).")
    billable = [i for i in detail.line_items if _billable(i)]
    charges = sum((i.net_amount_chf for i in billable if i.net_amount_chf > 0), Decimal(0))
    credits = sum((-i.net_amount_chf for i in billable if i.net_amount_chf < 0), Decimal(0))
    if charges != credits:
        problems.append(f"Rappenabstimmung fehlgeschlagen: Rechnungen CHF {charges} ≠ Gutschriften CHF {credits}.")
    with connect(db_path) as conn:
        recipients = _recipients(conn, run_id)
        open_storno = {r["participant_id"]: r["number"] for r in conn.execute(
            """
            SELECT d.participant_id, d.number FROM billing_documents d
            WHERE d.billing_run_id = ? AND d.status = ? AND d.cancel_number IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM document_deliveries x
                              WHERE x.document_id = d.id AND x.item = 'cancellation' AND x.status = 'sent')
            """, (run_id, DOC_STATUS_CANCELLED))}
    for item in billable:
        r = recipients.get(item.participant_id) or {}
        if not (r.get("label") and r.get("address_line") and r.get("postal_code") and r.get("city")):
            problems.append(f"{item.participant_label}: Postadresse fehlte beim Berechnen der Abrechnung — Adresse "
                            "ergänzen, Abrechnung stornieren und neu berechnen.")
        elif item.net_amount_chf > 0:
            try:
                _debtor(r["label"], r["address_line"], r["postal_code"], r["city"]).validated("Zahlungspflichtiger")
            except SwissQrError as exc:
                problems.append(f"{item.participant_label}: {exc} (QR-Rechnung) — Stammdaten korrigieren, "
                                "Abrechnung stornieren und neu berechnen.")
        if item.participant_id in open_storno:
            problems.append(f"{item.participant_label}: Stornobeleg zu {open_storno[item.participant_id]} ist noch "
                            "nicht zugestellt — erst danach kann ein Ersatzbeleg erstellt werden.")
    return problems


def recipient_changes(db_path: Path, run_id: int) -> list[str]:
    """Participants whose master data differs from the recipient frozen with the billing draft.

    The documents always use the frozen recipient (the contract party of the
    period); this list only tells the user that a correction made later is
    NOT on the documents unless the run is recomputed.
    """
    fields = {"label": "Name", "address_line": "Adresse", "postal_code": "PLZ", "city": "Ort", "email": "E-Mail"}
    changes = []
    with connect(db_path) as conn:
        frozen = _recipients(conn, run_id)
        for pid, snap in sorted(frozen.items()):
            row = conn.execute("SELECT label, email, address_line, postal_code, city FROM participants "
                               "WHERE participant_id = ?", (pid,)).fetchone()
            if row is None or not snap:
                continue
            diff = [label for key, label in fields.items() if (row[key] or "") != (snap.get(key) or "")]
            if diff:
                changes.append(f"{snap.get('label') or pid}: {', '.join(diff)} seit dem Berechnen geändert — die Belege "
                               "verwenden den Stand der Abrechnung. Soll die Änderung gelten: Abrechnung stornieren "
                               "und neu berechnen.")
    return changes


def documents_fingerprint(planned: list[dict]) -> str:
    """What the user approves: every planned document's number, recipient, amount and full text."""
    payload = [[p["number"], p["doc_type"], p["item"].participant_id, str(p["amount"]), p["address"],
                p["recipient"].get("email", ""), p["qr_payload"], p["profile_version"], _sha(p["html"])]
               for p in planned]
    return _sha(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _planned_documents(db_path: Path, run_id: int, render: Renderer, today: date, conn) -> list[dict]:
    """The documents that issuing would create now, rendered. Numbers are only final when `conn`
    is the write transaction that stores them."""
    detail = get_billing_run(db_path, run_id)
    run = detail.run
    creditor = _creditor(db_path, today, run.period_start, run.period_end)
    profile = get_payment_profile(db_path)
    ready = payment_profile_status(db_path) == PROFILE_READY
    community = get_community(db_path)
    recipients = _recipients(conn, run_id)
    active = {r["participant_id"] for r in conn.execute(
        "SELECT participant_id FROM billing_documents WHERE billing_run_id = ? AND status != ?",
        (run_id, DOC_STATUS_CANCELLED))}
    replaced = {r["participant_id"]: (r["id"], r["number"], r["cancel_number"]) for r in conn.execute(
        "SELECT id, participant_id, number, cancel_number FROM billing_documents "
        "WHERE billing_run_id = ? AND status = ? ORDER BY id", (run_id, DOC_STATUS_CANCELLED))}
    counters: dict[str, int] = {}
    planned = []
    for item in detail.line_items:
        if item.participant_id in active or not _billable(item):
            continue
        is_invoice = item.net_amount_chf > 0
        doc_type = DOC_TYPE_INVOICE if is_invoice else DOC_TYPE_CREDIT
        prefix = "R" if is_invoice else "G"
        if prefix not in counters:
            counters[prefix] = int(_next_number(conn, prefix, today.year).rsplit("-", 1)[1]) - 1
        counters[prefix] += 1
        number = f"{prefix}-{today.year}-{counters[prefix]:03d}"
        due = today + timedelta(days=int(creditor["payment_term_days"])) if is_invoice else None
        amount = abs(item.net_amount_chf)
        recipient = recipients.get(item.participant_id) or {}
        address = "\n".join(x for x in (recipient.get("address_line", ""),
                                         f"{recipient.get('postal_code', '')} {recipient.get('city', '')}".strip()) if x)
        replaces = replaced.get(item.participant_id)
        qr, ref_type, reference = None, "", ""
        if is_invoice and ready:
            ref_type, reference = reference_for(profile.iban, number)
            try:
                qr = PaymentPartView(SwissQrPayload.build(
                    iban=profile.iban, creditor=_profile_creditor(profile), amount=amount,
                    debtor=_debtor(recipient.get("label", ""), recipient.get("address_line", ""),
                                   recipient.get("postal_code", ""), recipient.get("city", "")),
                    reference_type=ref_type, reference=reference,
                    message=_qr_message(number, run.period_start, run.period_end)))
            except SwissQrError as exc:
                raise InvoicingError(f"QR-Rechnung {number}: {exc}")
        html = render("invoices/document.html", {
            "doc_type": doc_type, "number": number, "issue_date": today, "due_date": due, "amount": amount,
            "item": item, "run": run, "recipient": recipient, "creditor": creditor,
            "community_name": community.name if community else run.community_name,
            "contact_email": profile.contact_email or creditor["email"], "qr": qr,
            "qr_missing": is_invoice and not ready, "replaces": replaces,
        })
        planned.append({"item": item, "doc_type": doc_type, "number": number, "due": due, "amount": amount,
                        "recipient": recipient, "address": address, "html": html,
                        "replaces_id": replaces[0] if replaces else None,
                        "contract_versions": creditor["contract_versions"],
                        "qr_payload": qr.payload.text() if qr else "", "qr_reference": reference,
                        "qr_reference_type": ref_type, "qr_iban": profile.iban if qr else "",
                        "qr_payee": profile.payee if qr else "", "profile_version": profile.version if qr else ""})
    return planned


def preview_documents(db_path: Path, run_id: int, render: Renderer, *, today: date | None = None) -> list[dict]:
    """Rendered documents for the approval view (nothing stored; numbers provisional)."""
    with connect(db_path) as conn:
        return _planned_documents(db_path, run_id, render, today or local_today(), conn)


def missing_documents(db_path: Path, run_id: int) -> tuple[int, int]:
    """(issued, still missing) documents of a run — zero balances need none."""
    with connect(db_path) as conn:
        issued = {r["participant_id"] for r in conn.execute(
            "SELECT participant_id FROM billing_documents WHERE billing_run_id = ? AND status != ?",
            (run_id, DOC_STATUS_CANCELLED))}
    billable = {i.participant_id for i in get_billing_run(db_path, run_id).line_items if _billable(i)}
    return len(issued), len(billable - issued)


def issue_documents(db_path: Path, run_id: int, render: Renderer, *, today: date | None = None,
                    expected_fingerprint: str | None = None) -> list[BillingDocument]:
    """Issue the missing invoices/credit notes of a released run (all or nothing). Returns the new ones.

    All checks run while holding the write lock, so the run can't be
    cancelled (or anything else change) between check and write. With
    `expected_fingerprint` (from the approval view) only exactly the
    approved documents are stored; any difference requires a new preview.
    """
    today = today or local_today()
    created: list[int] = []
    with _write_txn(db_path) as conn:
        problems = issue_problems(db_path, run_id, today=today)
        if problems:
            raise InvoicingError("Belege können nicht erstellt werden: " + " ".join(problems))
        planned = _planned_documents(db_path, run_id, render, today, conn)
        if expected_fingerprint is not None and documents_fingerprint(planned) != expected_fingerprint:
            raise InvoicingError("Seit der Vorschau haben sich Belege geändert (Zahlungsangaben, Empfänger, Beträge "
                                 "oder Nummern) — bitte die Vorschau neu laden und erneut prüfen.")
        for p in planned:
            cur = conn.execute(
                """
                INSERT INTO billing_documents (billing_run_id, participant_id, doc_type, number, status,
                    amount_chf, issue_date, due_date, recipient_label, recipient_email, recipient_address,
                    replaces_document_id, contract_versions, snapshot_html, snapshot_sha256,
                    qr_payload, qr_reference, qr_reference_type, qr_render_version, qr_iban_snapshot,
                    qr_account_holder_snapshot, payment_profile_version, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, p["item"].participant_id, p["doc_type"], p["number"], DOC_STATUS_ISSUED, str(p["amount"]),
                 date_to_str(today), date_to_str(p["due"]), p["recipient"].get("label", ""),
                 p["recipient"].get("email", ""), p["address"], p["replaces_id"], p["contract_versions"],
                 p["html"], _sha(p["html"]), p["qr_payload"], p["qr_reference"], p["qr_reference_type"],
                 QR_RENDER_VERSION if p["qr_payload"] else "", p["qr_iban"], p["qr_payee"], p["profile_version"],
                 now_iso()),
            )
            created.append(cur.lastrowid)
            record_event(conn, "billing_document", cur.lastrowid, "issued",
                         f"{p['number']} {p['recipient'].get('label', '')} CHF {p['amount']} (Abrechnung {run_id})")
    logger.info("Invoicing: issued %d document(s) for run %d", len(created), run_id)
    return [get_document(db_path, doc_id) for doc_id in created]


# ── cancelling, delivery, reminder block ────────────────────────────────────


def cancel_document(db_path: Path, document_id: int, reason: str, render: Renderer, *,
                    today: date | None = None) -> BillingDocument:
    """Cancel a document nothing was booked on.

    Not yet delivered: simply cancelled. Delivered: a numbered cancellation
    notice (S-YYYY-NNN) referring to the original is frozen; it must be
    delivered before a replacement can be issued.
    """
    reason = reason.strip()
    if not reason:
        raise InvoicingError("Bitte einen Stornogrund angeben.")
    today = today or local_today()
    with _write_txn(db_path) as conn:
        doc = _load_document(conn, document_id)
        if doc.status == DOC_STATUS_CANCELLED:
            raise InvoicingError("Beleg ist bereits storniert.")
        _require_integrity(doc)
        if doc.payments:
            raise InvoicingError("Auf diesem Beleg sind Zahlungen/Auszahlungen gebucht — Storno nicht möglich. "
                                 "Korrekturen erfolgen mit einem Korrekturbeleg.")
        number, html = None, ""
        if doc.sent_at is not None:
            number = _next_number(conn, "S", today.year)
            creditor = _creditor(db_path, today, doc.period_start, doc.period_end)
            html = render("invoices/cancellation.html", {"doc": doc, "number": number, "issue_date": today,
                                                         "reason": reason, "creditor": creditor})
        conn.execute(
            "UPDATE billing_documents SET status = ?, cancel_reason = ?, cancelled_at = ?, cancel_number = ?, "
            "cancel_snapshot_html = ?, cancel_sha256 = ? WHERE id = ?",
            (DOC_STATUS_CANCELLED, reason, now_iso(), number, html, _sha(html) if html else "", document_id))
        conn.execute("UPDATE document_reminders SET discarded_at = ? WHERE document_id = ? AND discarded_at IS NULL",
                     (now_iso(), document_id))
        record_event(conn, "billing_document", document_id, "cancelled",
                     f"{doc.number}{' mit Stornobeleg ' + number if number else ''}: {reason}")
    return get_document(db_path, document_id)


def record_delivery(db_path: Path, document_id: int, method: str, *, status: str = "sent", detail: str = "",
                    item: str = "document", reminder_id: int | None = None) -> None:
    """Log one delivery attempt (manually recorded). Re-sending only adds a log entry — never a second claim."""
    if method not in DELIVERY_METHODS:
        raise InvoicingError("Unbekannter Zustellweg.")
    if status not in ("sent", "failed"):
        raise InvoicingError("Unbekannter Zustellstatus.")
    if item not in DELIVERY_ITEMS or (item == "reminder") != (reminder_id is not None):
        raise InvoicingError("Unbekanntes Zustellobjekt.")
    with _write_txn(db_path) as conn:
        doc = _load_document(conn, document_id)
        if status == "sent":
            _require_integrity(doc)
        if method == "email" and not doc.recipient_email:
            raise InvoicingError("Für diesen Empfänger ist keine E-Mail-Adresse hinterlegt — Zustellung per Papier.")
        if item == "cancellation":
            if not doc.cancel_number:
                raise InvoicingError("Zu diesem Beleg gibt es keinen Stornobeleg.")
            what = doc.cancel_number
        elif doc.status == DOC_STATUS_CANCELLED:
            raise InvoicingError("Stornierte Belege und ihre Mahnungen werden nicht mehr versendet.")
        elif item == "reminder":
            reminder = next((r for r in doc.reminders if r.id == reminder_id), None)
            if reminder is None or reminder.discarded_at is not None:
                raise InvoicingError("Mahnung gehört nicht zu diesem Beleg oder wurde verworfen.")
            if status == "sent":
                if doc.reminder_block_reason:
                    raise InvoicingError("Für diese Rechnung gilt eine Mahnsperre.")
                if doc.open_chf == 0:
                    raise InvoicingError("Die Rechnung ist inzwischen bezahlt — Mahnung nicht versenden, sondern verwerfen.")
                if doc.open_chf != reminder.open_amount_chf:
                    raise InvoicingError(
                        f"Seit dem Erstellen der Mahnung hat sich der offene Betrag geändert (jetzt CHF {doc.open_chf}) "
                        "— Mahnung verwerfen und neu erstellen.")
            what = reminder.label
        else:
            what = doc.number
        conn.execute(
            "INSERT INTO document_deliveries (document_id, reminder_id, item, method, status, detail, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (document_id, reminder_id, item, method, status, detail.strip(), now_iso()))
        record_event(conn, "billing_document", document_id, f"delivery_{status}",
                     f"{what} {DELIVERY_METHODS[method]} (manuell erfasst) {detail.strip()}".strip())


def set_reminder_block(db_path: Path, document_id: int, reason: str) -> None:
    """Set (non-empty reason) or lift (empty) the Mahnsperre of an invoice."""
    with _write_txn(db_path) as conn:
        doc = _load_document(conn, document_id)
        if not doc.is_invoice:
            raise InvoicingError("Mahnsperren gibt es nur für Rechnungen.")
        conn.execute("UPDATE billing_documents SET reminder_block_reason = ? WHERE id = ?", (reason.strip(), document_id))
        record_event(conn, "billing_document", document_id, "reminder_block", reason.strip() or "aufgehoben")


# ── payments, refunds and payouts ───────────────────────────────────────────


def _parse_amount(raw) -> Decimal:
    try:
        amount = Decimal(str(raw).replace("'", "").replace(",", ".").strip())
    except ArithmeticError:
        raise InvoicingError("Ungültiger Betrag.")
    if not amount.is_finite() or amount == 0 or amount != amount.quantize(_CENT):
        raise InvoicingError("Betrag muss ungleich 0 sein und höchstens zwei Nachkommastellen haben.")
    return amount


def _check_booking_date(doc: BillingDocument, booked_on: date, today: date) -> None:
    if booked_on > today:
        raise InvoicingError("Das Buchungsdatum liegt in der Zukunft.")
    if booked_on < doc.issue_date:
        raise InvoicingError(f"Das Buchungsdatum liegt vor dem Belegdatum ({doc.issue_date.strftime('%d.%m.%Y')}).")


def _check_duplicate(conn, doc: BillingDocument, kind: str, amount: Decimal, booked_on: date, reference: str,
                     confirm_duplicate: bool, note: str) -> None:
    """One bank transfer must not be booked twice: same reference, or same amount and date on the document."""
    if amount < 0:
        return
    ref = reference.strip().lower()
    clash = []
    if ref:
        clash = conn.execute(
            "SELECT p.document_id FROM document_payments p WHERE p.kind = ? AND lower(trim(p.reference)) = ? "
            "AND p.amount_chf NOT LIKE '-%'", (kind, ref)).fetchall()
    same = [p for p in doc.payments if p.kind == kind and p.amount_chf == amount and p.booked_on == booked_on]
    if (clash or same) and not confirm_duplicate:
        raise InvoicingError(
            "Mögliche Doppelbuchung: Diese Referenz bzw. derselbe Betrag am selben Tag ist bereits gebucht. "
            "Nur wenn es wirklich eine zweite Zahlung ist: «Zweite Buchung bestätigt» ankreuzen und begründen.")
    if (clash or same) and not note.strip():
        raise InvoicingError("Eine bestätigte zweite Buchung braucht eine Begründung.")


def _insert_payment(conn, doc: BillingDocument, kind: str, amount: Decimal, booked_on: date,
                    reference: str, note: str) -> None:
    conn.execute(
        "INSERT INTO document_payments (document_id, kind, amount_chf, booked_on, reference, note, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (doc.id, kind, str(amount), date_to_str(booked_on), reference.strip(), note.strip(), now_iso()))
    record_event(conn, "billing_document", doc.id, kind,
                 f"{doc.number} CHF {amount} am {booked_on.isoformat()} {reference.strip()} (manuell erfasst)".strip())


def _floor_shares(pool: Decimal, credits: list[Decimal]) -> list[Decimal]:
    """Each credit's share of `pool`, rounded DOWN to the cent.

    Monotone: a larger pool never lowers any share, so what was paid out
    stays covered. The sum never exceeds the pool; the few cents left over
    by rounding follow once everything was received (then share = credit).
    """
    total = sum(credits, Decimal(0))
    if total <= 0 or pool <= 0:
        return [Decimal(0) for _ in credits]
    if pool >= total:
        return list(credits)
    return [(pool * c / total).quantize(_CENT, rounding=ROUND_DOWN) for c in credits]


def _received(doc: BillingDocument) -> Decimal:
    """What an invoice contributes to the payout pool: money kept, capped at the invoice amount."""
    return min(max(doc.booked_chf, Decimal(0)), doc.amount_chf)


def _payout_status(conn, run_id: int) -> tuple[dict[int, PayoutStatus], Decimal, Decimal]:
    docs = [d for d in _query_documents(conn, ["d.billing_run_id = ?"], [run_id]) if d.status != DOC_STATUS_CANCELLED]
    credits = sorted((d for d in docs if not d.is_invoice), key=lambda d: d.id)
    received = sum((_received(d) for d in docs if d.is_invoice), Decimal(0))
    paid_out = sum((c.booked_chf for c in credits), Decimal(0))
    shares = _floor_shares(received, [c.amount_chf for c in credits])
    status = {
        c.id: PayoutStatus(document_id=c.id, amount_chf=c.amount_chf, entitled_chf=share, paid_out_chf=c.booked_chf,
                           available_chf=max(share - c.booked_chf, Decimal(0)))
        for c, share in zip(credits, shares)
    }
    return status, received, paid_out


def payout_status(db_path: Path, run_id: int) -> dict[int, PayoutStatus]:
    """Per credit note of a run: its share of the consumer payments received (§3.4)."""
    with connect(db_path) as conn:
        return _payout_status(conn, run_id)[0]


def record_payment(db_path: Path, document_id: int, amount, booked_on: date, *, reference: str = "",
                   note: str = "", confirm_duplicate: bool = False, today: date | None = None) -> BillingDocument:
    """Book a payment received on an invoice (bank or cash reference required).

    Partial and over-payments are allowed. A negative amount reverses a
    wrong booking (note required); it is refused when the money was already
    passed on to producers. An over-payment returned to the payer is a
    refund (record_refund), not a correction.
    """
    amount = _parse_amount(amount)
    today = today or local_today()
    with _write_txn(db_path) as conn:
        doc = _load_document(conn, document_id)
        _require_integrity(doc)
        if not doc.is_invoice:
            raise InvoicingError("Zahlungseingänge werden auf Rechnungen gebucht, Auszahlungen auf Gutschriften.")
        if doc.status == DOC_STATUS_CANCELLED:
            raise InvoicingError("Auf stornierte Belege kann nichts gebucht werden.")
        _check_booking_date(doc, booked_on, today)
        if amount > 0 and not reference.strip():
            raise InvoicingError("Bitte die Referenz der Zahlung angeben (Bankbuchung, «bar» mit Quittungsnummer …).")
        if amount < 0:
            if not note.strip():
                raise InvoicingError("Eine Korrekturbuchung braucht eine Begründung.")
            payments = sum((p.amount_chf for p in doc.payments if p.kind == PAYMENT_KIND_PAYMENT), Decimal(0))
            if payments + amount < 0 or doc.booked_chf + amount < 0:
                raise InvoicingError("Die Korrektur ist grösser als die gebuchten Zahlungen.")
            status, received, paid_out = _payout_status(conn, doc.billing_run_id)
            after = received - _received(doc) + min(max(doc.booked_chf + amount, Decimal(0)), doc.amount_chf)
            credit_ids = sorted(status)
            new_shares = _floor_shares(after, [status[i].amount_chf for i in credit_ids])
            over = [i for i, share in zip(credit_ids, new_shares) if status[i].paid_out_chf > share]
            if after < paid_out or over:
                raise InvoicingError(
                    "Diese Zahlung wurde bereits an Produzenten weitergegeben — nach der Korrektur wären "
                    f"{len(over) or 'alle'} Produzent(en) überzahlt. Korrektur erst nach geregelter Rückforderung "
                    "bzw. Verrechnung möglich (Vertrag Ziff. 3.4).")
        _check_duplicate(conn, doc, PAYMENT_KIND_PAYMENT, amount, booked_on, reference, confirm_duplicate, note)
        _insert_payment(conn, doc, PAYMENT_KIND_PAYMENT, amount, booked_on, reference, note)
    return get_document(db_path, document_id)


def record_refund(db_path: Path, document_id: int, amount, booked_on: date, *, reference: str,
                  note: str = "", today: date | None = None) -> BillingDocument:
    """Record an over-payment returned to the payer (a real outgoing transfer, bank reference required)."""
    amount = _parse_amount(amount)
    today = today or local_today()
    if amount < 0:
        raise InvoicingError("Rückerstattungen als positiven Betrag erfassen.")
    if not reference.strip():
        raise InvoicingError("Bitte die Bankreferenz der Rückerstattung angeben.")
    with _write_txn(db_path) as conn:
        doc = _load_document(conn, document_id)
        _require_integrity(doc)
        if not doc.is_invoice:
            raise InvoicingError("Rückerstattungen gibt es nur auf Rechnungen.")
        _check_booking_date(doc, booked_on, today)
        ref = reference.strip().lower()
        if conn.execute("SELECT 1 FROM document_payments WHERE kind = ? AND lower(trim(reference)) = ?",
                        (PAYMENT_KIND_REFUND, ref)).fetchone():
            raise InvoicingError("Diese Bankreferenz ist bereits als Rückerstattung gebucht — eine zweite "
                                 "Rückerstattung braucht die Referenz ihrer eigenen Überweisung.")
        overpaid = doc.booked_chf - doc.amount_chf
        if amount > overpaid:
            raise InvoicingError(
                f"Zurückerstattet werden kann höchstens die Überzahlung von CHF {max(overpaid, Decimal(0))}.")
        _insert_payment(conn, doc, PAYMENT_KIND_REFUND, -amount, booked_on, reference, note)
    return get_document(db_path, document_id)


def record_payout(db_path: Path, document_id: int, amount, booked_on: date, *, reference: str,
                  note: str = "", confirm_duplicate: bool = False, today: date | None = None) -> BillingDocument:
    """Record a payout made by the representative's bank (never executed by Shareomat).

    Checked in the same write transaction against the producer's share AND
    the run total: payouts never exceed the consumer payments received
    (contract §3.4, no pre-financing). The bank reference is mandatory.
    """
    amount = _parse_amount(amount)
    today = today or local_today()
    if not reference.strip():
        raise InvoicingError("Bitte die Bankreferenz der Auszahlung angeben.")
    with _write_txn(db_path) as conn:
        doc = _load_document(conn, document_id)
        _require_integrity(doc)
        if doc.is_invoice:
            raise InvoicingError("Auszahlungen werden auf Gutschriften gebucht.")
        if doc.status == DOC_STATUS_CANCELLED:
            raise InvoicingError("Auf stornierte Belege kann nichts gebucht werden.")
        _check_booking_date(doc, booked_on, today)
        if amount < 0:
            if not note.strip():
                raise InvoicingError("Eine Korrekturbuchung braucht eine Begründung.")
            if doc.booked_chf + amount < 0:
                raise InvoicingError("Die Korrektur ist grösser als die gebuchten Auszahlungen.")
        else:
            status, received, paid_out = _payout_status(conn, doc.billing_run_id)
            available = min(status[doc.id].available_chf, max(received - paid_out, Decimal(0)))
            if amount > available:
                raise InvoicingError(
                    f"Auszahlbar sind derzeit höchstens CHF {available} — Gutschriften werden nur aus eingegangenen "
                    "Zahlungen der LEG-Bezüger ausbezahlt (Vertrag Ziff. 3.4).")
        _check_duplicate(conn, doc, PAYMENT_KIND_PAYOUT, amount, booked_on, reference, confirm_duplicate, note)
        _insert_payment(conn, doc, PAYMENT_KIND_PAYOUT, amount, booked_on, reference, note)
    return get_document(db_path, document_id)


# ── reminders (Mahnwesen) ───────────────────────────────────────────────────


def _proposal_for(doc: BillingDocument, today: date) -> ReminderProposal | None:
    if doc.state(today) != "overdue" or doc.reminder_block_reason or doc.sent_at is None:
        return None
    active = doc.active_reminders
    level = len(active) + 1
    if level > len(REMINDER_OFFSETS_DAYS):
        return None
    earliest = doc.due_date + timedelta(days=REMINDER_OFFSETS_DAYS[level - 1])
    if active:
        previous = active[-1]
        if previous.sent_at is None:
            return None
        if previous.new_due_date is not None:
            earliest = max(earliest, previous.new_due_date + timedelta(days=REMINDER_GRACE_DAYS))
    return ReminderProposal(document=doc, level=level, due_since=earliest) if today >= earliest else None


def reminder_proposals(db_path: Path, *, today: date | None = None) -> list[ReminderProposal]:
    """Overdue, delivered, unblocked invoices whose next reminder level is due.

    Level n from due date + REMINDER_OFFSETS_DAYS[n-1], and never before the
    deadline of the previous reminder + REMINDER_GRACE_DAYS; never for a
    paid/cancelled/blocked invoice, and only after the previous reminder was
    actually sent.
    """
    today = today or local_today()
    return [p for doc in list_documents(db_path, doc_type=DOC_TYPE_INVOICE)
            if (p := _proposal_for(doc, today)) is not None]


def create_reminder(db_path: Path, document_id: int, render: Renderer, *, today: date | None = None) -> DocumentReminder:
    """Create the next due reminder, frozen like the invoice; debtor and address from the invoice snapshot."""
    today = today or local_today()
    problems = payment_problems(db_path)
    if problems:
        raise InvoicingError(problems[0])
    profile = get_payment_profile(db_path)
    with _write_txn(db_path) as conn:
        doc = _load_document(conn, document_id)
        _require_integrity(doc)
        proposal = _proposal_for(doc, today)
        if proposal is None:
            raise InvoicingError("Für diese Rechnung ist derzeit keine Mahnung fällig (bezahlt, gesperrt, nicht "
                                 "versendet, vorherige Mahnung nicht versendet oder deren Frist läuft noch).")
        # Same claim, same reference as the invoice; only the open amount is new.
        if doc.qr_reference:
            if doc.qr_reference_type != reference_type_for(profile.iban):
                raise InvoicingError(
                    f"Die Referenz der Rechnung ({doc.qr_reference_type}) passt nicht zum aktuellen Konto "
                    f"({reference_type_for(profile.iban)}) — Zahlungserinnerung manuell mit den Kontodaten der "
                    "Rechnung erstellen oder Rechnung stornieren und neu ausstellen.")
            ref_type, reference = doc.qr_reference_type, doc.qr_reference
        else:
            ref_type, reference = reference_for(profile.iban, doc.number)
        try:
            qr = PaymentPartView(SwissQrPayload.build(
                iban=profile.iban, creditor=_profile_creditor(profile), amount=doc.open_chf,
                debtor=_debtor_from_document(doc), reference_type=ref_type, reference=reference,
                message=_qr_message(doc.number, doc.period_start, doc.period_end)))
        except SwissQrError as exc:
            raise InvoicingError(f"QR-Zahlteil der Mahnung: {exc}")
        creditor = _creditor(db_path, today, doc.period_start, doc.period_end)
        new_due = today + timedelta(days=REMINDER_PAYMENT_DAYS)
        html = render("invoices/reminder.html", {
            "doc": doc, "level": proposal.level, "label": REMINDER_LABELS[proposal.level], "issue_date": today,
            "open_amount": doc.open_chf, "new_due_date": new_due, "creditor": creditor,
            "community_name": (get_community(db_path).name if get_community(db_path) else ""),
            "contact_email": profile.contact_email or creditor["email"], "qr": qr, "qr_missing": False,
        })
        cur = conn.execute(
            "INSERT INTO document_reminders (document_id, level, issue_date, open_amount_chf, snapshot_html, "
            "snapshot_sha256, new_due_date, qr_payload, qr_reference, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (doc.id, proposal.level, date_to_str(today), str(doc.open_chf), html, _sha(html),
             date_to_str(new_due), qr.payload.text(), reference, now_iso()))
        reminder_id = cur.lastrowid
        record_event(conn, "billing_document", doc.id, "reminder_created",
                     f"{doc.number} {REMINDER_LABELS[proposal.level]} offen CHF {doc.open_chf}")
    return get_reminder(db_path, reminder_id)


def discard_reminder(db_path: Path, reminder_id: int, reason: str) -> None:
    """Discard an unsent reminder (paid meanwhile, amount changed, ...); it stays archived."""
    if not reason.strip():
        raise InvoicingError("Bitte einen Grund angeben.")
    with _write_txn(db_path) as conn:
        row = conn.execute("SELECT document_id FROM document_reminders WHERE id = ? AND discarded_at IS NULL",
                           (reminder_id,)).fetchone()
        if row is None:
            raise InvoicingError("Mahnung nicht gefunden oder bereits verworfen.")
        doc = _load_document(conn, row["document_id"])
        if next(r for r in doc.reminders if r.id == reminder_id).sent_at is not None:
            raise InvoicingError("Eine versendete Mahnung kann nicht verworfen werden.")
        conn.execute("UPDATE document_reminders SET discarded_at = ? WHERE id = ?", (now_iso(), reminder_id))
        record_event(conn, "billing_document", doc.id, "reminder_discarded", f"Mahnung {reminder_id}: {reason.strip()}")


def get_reminder(db_path: Path, reminder_id: int) -> DocumentReminder:
    with connect(db_path) as conn:
        row = conn.execute("SELECT document_id FROM document_reminders WHERE id = ?", (reminder_id,)).fetchone()
        if row is None:
            raise InvoicingError(f"Mahnung {reminder_id} wurde nicht gefunden.")
        return next(r for r in _load_document(conn, row["document_id"]).reminders if r.id == reminder_id)
