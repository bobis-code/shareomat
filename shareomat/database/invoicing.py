# -*- coding: utf-8 -*-
"""
File: shareomat/database/invoicing.py

Purpose:
    "Rechnungen & Gutschriften": issue one document per participant from a
    released billing run, and keep the append-only logs of payments
    received, producer payouts, deliveries and payment reminders.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Contract §3.4 (Mustervertrag + decision of 27.10.2026):
      - Prosumers get ONE document with the net balance: invoice when the
        charge exceeds the credit, credit note otherwise.
      - Credits are paid out only from, and as far as, consumer payments for
        the same billing run arrived, distributed pro rata to the credits
        (largest-remainder cents). The representative never pre-finances,
        so a payout above the available share is refused.

    Issuing is all-or-nothing per run and idempotent: a second call only
    issues documents for participants that have none (e.g. after a
    cancelled document was corrected). A document can be cancelled only
    while nothing was booked on it; its number is never reused.

    Rendering is injected (`render(template_name, context) -> html`) so this
    module stays free of the web layer; the HTML is frozen with its SHA-256.

    Not yet here (later phases): e-mail sending via SMTP, Swiss QR bill,
    pain.001 payout files, correction documents for corrected grid-operator
    data.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from collections.abc import Callable
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from shareomat.database.audit import record_event
from shareomat.database.billing import get_billing_run
from shareomat.database.community import get_community
from shareomat.database.contract_versions import get_contract_version_for_date
from shareomat.database.participants import get_participant, list_participants
from shareomat.database.settings import get_setting, set_setting
from shareomat.database.sqlite import connect, date_to_str, now_iso, str_to_date
from shareomat.leg_const import BILLING_STATUS_RELEASED
from shareomat.models.invoicing import (
    DELIVERY_METHODS,
    DOC_STATUS_CANCELLED,
    DOC_STATUS_ISSUED,
    DOC_TYPE_CREDIT,
    DOC_TYPE_INVOICE,
    PAYMENT_KIND_PAYMENT,
    PAYMENT_KIND_PAYOUT,
    REMINDER_LABELS,
    REMINDER_OFFSETS_DAYS,
    BillingDocument,
    DocumentDelivery,
    DocumentPayment,
    DocumentReminder,
    PayoutStatus,
    ReminderProposal,
)

logger = logging.getLogger(__name__)

_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
_CENT = Decimal("0.01")

Renderer = Callable[[str, dict], str]

SETTING_PAYEE = "invoice_payee"
SETTING_IBAN = "invoice_iban"


class InvoicingError(Exception):
    """A document action was refused; the message is shown to the user."""


def local_today() -> date:
    return datetime.now(_TZ).date()


# ── payment details (Zahlungsangaben) ───────────────────────────────────────


def normalize_iban(raw: str) -> str:
    return re.sub(r"\s+", "", raw or "").upper()


def iban_is_valid(iban: str) -> bool:
    """ISO 13616 mod-97 check; CH/LI IBANs must have 21 characters."""
    iban = normalize_iban(iban)
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{10,30}", iban):
        return False
    if iban[:2] in ("CH", "LI") and len(iban) != 21:
        return False
    digits = "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])
    return int(digits) % 97 == 1


def format_iban(iban: str) -> str:
    iban = normalize_iban(iban)
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def get_payment_details(db_path: Path) -> tuple[str, str]:
    """(payee, IBAN) printed on invoices; empty strings when not configured."""
    return get_setting(db_path, SETTING_PAYEE) or "", get_setting(db_path, SETTING_IBAN) or ""


def save_payment_details(db_path: Path, payee: str, iban: str) -> None:
    payee = payee.strip()
    iban = normalize_iban(iban)
    if not payee:
        raise InvoicingError("Bitte den Kontoinhaber (Zahlungsempfänger) angeben.")
    if not iban_is_valid(iban):
        raise InvoicingError("Die IBAN ist ungültig (Prüfziffer oder Länge stimmt nicht).")
    set_setting(db_path, SETTING_PAYEE, payee)
    set_setting(db_path, SETTING_IBAN, iban)


# ── issuing ─────────────────────────────────────────────────────────────────


def _next_number(conn, prefix: str, year: int) -> str:
    stem = f"{prefix}-{year}-"
    rows = conn.execute("SELECT number FROM billing_documents WHERE number LIKE ?", (stem + "%",)).fetchall()
    used = [int(r["number"][len(stem):]) for r in rows if r["number"][len(stem):].isdigit()]
    return f"{stem}{(max(used) + 1 if used else 1):03d}"


def issue_problems(db_path: Path, run_id: int) -> list[str]:
    """Reasons the documents of a run can't be issued yet (empty = ready)."""
    detail = get_billing_run(db_path, run_id)
    problems: list[str] = []
    if detail.run.status != BILLING_STATUS_RELEASED:
        problems.append("Nur freigegebene Abrechnungen können verrechnet werden.")
    payee, iban = get_payment_details(db_path)
    if not payee or not iban:
        problems.append("Zahlungsangaben (Kontoinhaber, IBAN) fehlen.")
    creditor = _creditor(db_path, detail.run.period_end)
    if not creditor["name"] or not creditor["address_line"] or not creditor["city"]:
        problems.append("Name und Adresse des LEG-Vertreters fehlen (Vertrag bzw. Gemeinschaft).")
    participants = {p.participant_id: p for p in list_participants(db_path)}
    for item in detail.line_items:
        if item.charge_chf == 0 and item.credit_chf == 0:
            continue
        p = participants.get(item.participant_id)
        if p is None:
            problems.append(f"Teilnehmer {item.participant_id} existiert nicht mehr.")
        elif not (p.address_line and p.postal_code and p.city):
            problems.append(f"{p.label}: Postadresse unvollständig.")
    return problems


def _creditor(db_path: Path, as_of: date | None) -> dict:
    version = get_contract_version_for_date(db_path, as_of) if as_of else None
    if version is not None and version.representative_name:
        return {"name": version.representative_name, "address_line": version.representative_address_line,
                "postal_code": version.representative_postal_code, "city": version.representative_city,
                "email": version.representative_email, "contract_version": version.version,
                "payment_term_days": version.payment_term_days}
    community = get_community(db_path)
    return {"name": community.name if community else "", "address_line": community.address_line if community else "",
            "postal_code": community.postal_code if community else "", "city": community.city if community else "",
            "email": "", "contract_version": None, "payment_term_days": 30}


def issue_documents(db_path: Path, run_id: int, render: Renderer, *, today: date | None = None) -> list[BillingDocument]:
    """Issue the missing invoices/credit notes of a released run (all or nothing). Returns the new ones."""
    problems = issue_problems(db_path, run_id)
    if problems:
        raise InvoicingError("Belege können nicht erstellt werden: " + " ".join(problems))
    today = today or local_today()
    detail = get_billing_run(db_path, run_id)
    run = detail.run
    creditor = _creditor(db_path, run.period_end)
    payee, iban = get_payment_details(db_path)
    community = get_community(db_path)
    participants = {p.participant_id: p for p in list_participants(db_path)}
    created: list[int] = []
    with connect(db_path) as conn:
        with conn:
            existing = {r["participant_id"] for r in conn.execute(
                "SELECT participant_id FROM billing_documents WHERE billing_run_id = ? AND status != ?",
                (run_id, DOC_STATUS_CANCELLED))}
            for item in detail.line_items:
                if item.participant_id in existing or (item.charge_chf == 0 and item.credit_chf == 0):
                    continue
                p = participants[item.participant_id]
                is_invoice = item.net_amount_chf > 0 or (item.net_amount_chf == 0 and item.charge_chf > 0)
                doc_type = DOC_TYPE_INVOICE if is_invoice else DOC_TYPE_CREDIT
                number = _next_number(conn, "R" if is_invoice else "G", today.year)
                due = today + timedelta(days=int(creditor["payment_term_days"])) if is_invoice else None
                amount = abs(item.net_amount_chf)
                html = render("invoices/document.html", {
                    "doc_type": doc_type, "number": number, "issue_date": today, "due_date": due,
                    "amount": amount, "item": item, "run": run, "participant": p, "creditor": creditor,
                    "community_name": community.name if community else run.community_name,
                    "payee": payee, "iban": format_iban(iban),
                })
                cur = conn.execute(
                    """
                    INSERT INTO billing_documents (billing_run_id, participant_id, doc_type, number, status,
                        amount_chf, issue_date, due_date, recipient_label, recipient_email, snapshot_html,
                        snapshot_sha256, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (run_id, p.participant_id, doc_type, number, DOC_STATUS_ISSUED, str(amount),
                     date_to_str(today), date_to_str(due), p.label, p.email, html,
                     hashlib.sha256(html.encode("utf-8")).hexdigest(), now_iso()),
                )
                created.append(cur.lastrowid)
                record_event(conn, "billing_document", cur.lastrowid, "issued",
                             f"{number} {p.label} CHF {amount} (Abrechnung {run_id})")
    logger.info("Invoicing: issued %d document(s) for run %d", len(created), run_id)
    return [get_document(db_path, doc_id) for doc_id in created]


# ── reading ─────────────────────────────────────────────────────────────────

_DOC_SELECT = """
    SELECT d.*, bp.period_start AS bp_start, bp.period_end AS bp_end,
           r.local_received_kwh AS rec_received, r.local_supplied_kwh AS rec_supplied
    FROM billing_documents d
    JOIN billing_runs br ON br.id = d.billing_run_id
    JOIN billing_periods bp ON bp.id = br.billing_period_id
    LEFT JOIN billing_records r ON r.billing_run_id = d.billing_run_id AND r.participant_id = d.participant_id
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
                         status=r["status"], detail=r["detail"], recorded_at=_ts(r["recorded_at"]))
        for r in conn.execute("SELECT * FROM document_deliveries WHERE document_id = ? ORDER BY id", (doc.id,))
    ]
    sent = [d.recorded_at for d in doc.deliveries if d.status == "sent" and d.reminder_id is None]
    doc.sent_at = sent[0] if sent else None
    doc.reminders = []
    for r in conn.execute("SELECT * FROM document_reminders WHERE document_id = ? ORDER BY level", (doc.id,)):
        reminder_sent = [d.recorded_at for d in doc.deliveries if d.status == "sent" and d.reminder_id == r["id"]]
        doc.reminders.append(DocumentReminder(
            id=r["id"], document_id=r["document_id"], level=r["level"], issue_date=str_to_date(r["issue_date"]),
            open_amount_chf=Decimal(r["open_amount_chf"]), snapshot_html=r["snapshot_html"],
            snapshot_sha256=r["snapshot_sha256"], sent_at=reminder_sent[0] if reminder_sent else None))


def _row_to_document(row) -> BillingDocument:
    is_invoice = row["doc_type"] == DOC_TYPE_INVOICE
    kwh = row["rec_received"] if is_invoice else row["rec_supplied"]
    return BillingDocument(
        id=row["id"], billing_run_id=row["billing_run_id"], participant_id=row["participant_id"],
        doc_type=row["doc_type"], number=row["number"], status=row["status"], amount_chf=Decimal(row["amount_chf"]),
        issue_date=str_to_date(row["issue_date"]), due_date=str_to_date(row["due_date"]),
        recipient_label=row["recipient_label"], recipient_email=row["recipient_email"],
        snapshot_html=row["snapshot_html"], snapshot_sha256=row["snapshot_sha256"],
        reminder_block_reason=row["reminder_block_reason"], cancel_reason=row["cancel_reason"],
        cancelled_at=_ts(row["cancelled_at"]), period_start=str_to_date(row["bp_start"]),
        period_end=str_to_date(row["bp_end"]), local_kwh=Decimal(kwh or "0"),
    )


def list_documents(db_path: Path, *, doc_type: str | None = None, run_id: int | None = None) -> list[BillingDocument]:
    """All documents, newest first, with their logs loaded."""
    where, params = [], []
    if doc_type:
        where.append("d.doc_type = ?")
        params.append(doc_type)
    if run_id is not None:
        where.append("d.billing_run_id = ?")
        params.append(run_id)
    sql = _DOC_SELECT + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY d.issue_date DESC, d.number DESC"
    with connect(db_path) as conn:
        docs = [_row_to_document(r) for r in conn.execute(sql, params).fetchall()]
        for doc in docs:
            _load_logs(conn, doc)
    return docs


def get_document(db_path: Path, document_id: int) -> BillingDocument:
    with connect(db_path) as conn:
        row = conn.execute(_DOC_SELECT + " WHERE d.id = ?", (document_id,)).fetchone()
        if row is None:
            raise InvoicingError(f"Beleg {document_id} wurde nicht gefunden.")
        doc = _row_to_document(row)
        _load_logs(conn, doc)
    return doc


def missing_documents(db_path: Path, run_id: int) -> tuple[int, int]:
    """(issued, still missing) documents of a run — participants with nothing to bill need none."""
    with connect(db_path) as conn:
        issued = {r["participant_id"] for r in conn.execute(
            "SELECT participant_id FROM billing_documents WHERE billing_run_id = ? AND status != ?",
            (run_id, DOC_STATUS_CANCELLED))}
    billable = {i.participant_id for i in get_billing_run(db_path, run_id).line_items
                if i.charge_chf != 0 or i.credit_chf != 0}
    return len(issued), len(billable - issued)


# ── cancelling, delivery, reminder block ────────────────────────────────────


def cancel_document(db_path: Path, document_id: int, reason: str) -> BillingDocument:
    """Cancel a document nothing was booked on; the run can then be re-issued for that participant."""
    reason = reason.strip()
    if not reason:
        raise InvoicingError("Bitte einen Stornogrund angeben.")
    doc = get_document(db_path, document_id)
    if doc.status == DOC_STATUS_CANCELLED:
        raise InvoicingError("Beleg ist bereits storniert.")
    if doc.payments:
        raise InvoicingError("Auf diesem Beleg sind Zahlungen/Auszahlungen gebucht — Storno nicht möglich. "
                             "Korrekturen erfolgen mit einem Korrekturbeleg.")
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE billing_documents SET status = ?, cancel_reason = ?, cancelled_at = ? WHERE id = ?",
                         (DOC_STATUS_CANCELLED, reason, now_iso(), document_id))
            record_event(conn, "billing_document", document_id, "cancelled", f"{doc.number}: {reason}")
    return get_document(db_path, document_id)


def record_delivery(db_path: Path, document_id: int, method: str, *, status: str = "sent", detail: str = "",
                    reminder_id: int | None = None) -> None:
    """Log one delivery attempt. Re-sending only adds a log entry — never a second claim."""
    if method not in DELIVERY_METHODS:
        raise InvoicingError("Unbekannter Zustellweg.")
    if status not in ("sent", "failed"):
        raise InvoicingError("Unbekannter Zustellstatus.")
    doc = get_document(db_path, document_id)
    if doc.status == DOC_STATUS_CANCELLED:
        raise InvoicingError("Stornierte Belege werden nicht versendet.")
    if method == "email" and not doc.recipient_email:
        raise InvoicingError("Für diesen Empfänger ist keine E-Mail-Adresse hinterlegt — Zustellung per Papier.")
    if reminder_id is not None and reminder_id not in {r.id for r in doc.reminders}:
        raise InvoicingError("Mahnung gehört nicht zu diesem Beleg.")
    with connect(db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO document_deliveries (document_id, reminder_id, method, status, detail, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (document_id, reminder_id, method, status, detail.strip(), now_iso()))
            what = f"Mahnung {reminder_id}" if reminder_id else doc.number
            record_event(conn, "billing_document", document_id, f"delivery_{status}",
                         f"{what} {DELIVERY_METHODS[method]} {detail.strip()}".strip())


def set_reminder_block(db_path: Path, document_id: int, reason: str) -> None:
    """Set (non-empty reason) or lift (empty) the Mahnsperre of an invoice."""
    doc = get_document(db_path, document_id)
    if not doc.is_invoice:
        raise InvoicingError("Mahnsperren gibt es nur für Rechnungen.")
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE billing_documents SET reminder_block_reason = ? WHERE id = ?",
                         (reason.strip(), document_id))
            record_event(conn, "billing_document", document_id, "reminder_block",
                         reason.strip() or "aufgehoben")


# ── payments and payouts ────────────────────────────────────────────────────


def _parse_amount(raw) -> Decimal:
    try:
        amount = Decimal(str(raw).replace("'", "").replace(",", ".").strip())
    except ArithmeticError:
        raise InvoicingError("Ungültiger Betrag.")
    if not amount.is_finite() or amount == 0 or amount != amount.quantize(_CENT):
        raise InvoicingError("Betrag muss ungleich 0 sein und höchstens zwei Nachkommastellen haben.")
    return amount


def _insert_payment(db_path: Path, doc: BillingDocument, kind: str, amount: Decimal, booked_on: date,
                    reference: str, note: str) -> None:
    with connect(db_path) as conn:
        with conn:
            conn.execute(
                "INSERT INTO document_payments (document_id, kind, amount_chf, booked_on, reference, note, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc.id, kind, str(amount), date_to_str(booked_on), reference.strip(), note.strip(), now_iso()))
            record_event(conn, "billing_document", doc.id, kind,
                         f"{doc.number} CHF {amount} am {booked_on.isoformat()} {reference.strip()}".strip())


def record_payment(db_path: Path, document_id: int, amount, booked_on: date, *, reference: str = "",
                   note: str = "") -> BillingDocument:
    """Book a payment received on an invoice. Partial and over-payments are allowed; a negative
    amount reverses a wrong booking and needs a note. The total can never become negative."""
    amount = _parse_amount(amount)
    doc = get_document(db_path, document_id)
    if not doc.is_invoice:
        raise InvoicingError("Zahlungseingänge werden auf Rechnungen gebucht, Auszahlungen auf Gutschriften.")
    if doc.status == DOC_STATUS_CANCELLED:
        raise InvoicingError("Auf stornierte Belege kann nichts gebucht werden.")
    if amount < 0:
        if not note.strip():
            raise InvoicingError("Eine Korrekturbuchung braucht eine Begründung.")
        if doc.booked_chf + amount < 0:
            raise InvoicingError("Die Korrektur ist grösser als die gebuchten Zahlungen.")
        if min(doc.booked_chf + amount, doc.amount_chf) < _paid_out_against(db_path, doc.billing_run_id, excluding=doc.id):
            raise InvoicingError("Diese Zahlung wurde bereits an Produzenten weitergegeben — Korrektur nicht möglich.")
    _insert_payment(db_path, doc, PAYMENT_KIND_PAYMENT, amount, booked_on, reference, note)
    return get_document(db_path, document_id)


def _paid_out_against(db_path: Path, run_id: int, *, excluding: int) -> Decimal:
    """Payouts of the run that the received payments of the OTHER invoices can't cover any more."""
    docs = list_documents(db_path, run_id=run_id)
    received_others = sum((min(d.booked_chf, d.amount_chf) for d in docs
                           if d.is_invoice and d.status != DOC_STATUS_CANCELLED and d.id != excluding), Decimal(0))
    paid_out = sum((d.booked_chf for d in docs if not d.is_invoice), Decimal(0))
    return max(paid_out - received_others, Decimal(0))


def _largest_remainder_cents(pool: Decimal, weights: list[Decimal]) -> list[Decimal]:
    """Split `pool` (0.01) in proportion to `weights`; the cents add up exactly to `pool`."""
    total = sum(weights, Decimal(0))
    if total <= 0 or pool <= 0:
        return [Decimal(0) for _ in weights]
    exact = [pool * w / total for w in weights]
    floors = [e.quantize(_CENT, rounding=ROUND_DOWN) for e in exact]
    rest = int(((pool - sum(floors, Decimal(0))) / _CENT).to_integral_value())
    order = sorted(range(len(weights)), key=lambda i: (-(exact[i] - floors[i]), i))
    for i in order[:rest]:
        floors[i] += _CENT
    return floors


def payout_status(db_path: Path, run_id: int) -> dict[int, PayoutStatus]:
    """Per credit note of a run: its pro-rata share of the consumer payments received (§3.4).

    Received = what was paid on the run's invoices, each capped at the
    invoice amount (an over-payment belongs to the payer, not the producers).
    """
    docs = [d for d in list_documents(db_path, run_id=run_id) if d.status != DOC_STATUS_CANCELLED]
    credits = sorted((d for d in docs if not d.is_invoice), key=lambda d: d.id)
    received = sum((min(max(d.booked_chf, Decimal(0)), d.amount_chf) for d in docs if d.is_invoice), Decimal(0))
    pool = min(received, sum((c.amount_chf for c in credits), Decimal(0)))
    shares = _largest_remainder_cents(pool, [c.amount_chf for c in credits])
    return {
        c.id: PayoutStatus(document_id=c.id, amount_chf=c.amount_chf, entitled_chf=share, paid_out_chf=c.booked_chf,
                           available_chf=max(share - c.booked_chf, Decimal(0)))
        for c, share in zip(credits, shares)
    }


def record_payout(db_path: Path, document_id: int, amount, booked_on: date, *, reference: str,
                  note: str = "") -> BillingDocument:
    """Record a payout made by the representative's bank (never executed by Shareomat).

    Refused above the share of received consumer payments — contract §3.4:
    no pre-financing. The bank reference is mandatory.
    """
    amount = _parse_amount(amount)
    doc = get_document(db_path, document_id)
    if doc.is_invoice:
        raise InvoicingError("Auszahlungen werden auf Gutschriften gebucht.")
    if doc.status == DOC_STATUS_CANCELLED:
        raise InvoicingError("Auf stornierte Belege kann nichts gebucht werden.")
    if not reference.strip():
        raise InvoicingError("Bitte die Bankreferenz der Auszahlung angeben.")
    if amount < 0:
        if not note.strip():
            raise InvoicingError("Eine Korrekturbuchung braucht eine Begründung.")
        if doc.booked_chf + amount < 0:
            raise InvoicingError("Die Korrektur ist grösser als die gebuchten Auszahlungen.")
    else:
        available = payout_status(db_path, doc.billing_run_id)[doc.id].available_chf
        if amount > available:
            raise InvoicingError(
                f"Auszahlbar sind derzeit höchstens CHF {available} — Gutschriften werden nur aus eingegangenen "
                "Zahlungen der LEG-Bezüger ausbezahlt (Vertrag Ziff. 3.4).")
    _insert_payment(db_path, doc, PAYMENT_KIND_PAYOUT, amount, booked_on, reference, note)
    return get_document(db_path, document_id)


# ── reminders (Mahnwesen) ───────────────────────────────────────────────────


def reminder_proposals(db_path: Path, *, today: date | None = None) -> list[ReminderProposal]:
    """Overdue, delivered, unblocked invoices whose next reminder level is due.

    Level n is proposed from due date + REMINDER_OFFSETS_DAYS[n-1]; never
    before the due date, never for a paid/cancelled/blocked invoice, and
    only after the previous reminder was actually sent.
    """
    today = today or local_today()
    proposals: list[ReminderProposal] = []
    for doc in list_documents(db_path, doc_type=DOC_TYPE_INVOICE):
        if doc.state(today) != "overdue" or doc.reminder_block_reason or doc.sent_at is None:
            continue
        level = len(doc.reminders) + 1
        if level > len(REMINDER_OFFSETS_DAYS):
            continue
        if doc.reminders and doc.reminders[-1].sent_at is None:
            continue
        due_since = doc.due_date + timedelta(days=REMINDER_OFFSETS_DAYS[level - 1])
        if today >= due_since:
            proposals.append(ReminderProposal(document=doc, level=level, due_since=due_since))
    return proposals


def create_reminder(db_path: Path, document_id: int, render: Renderer, *, today: date | None = None) -> DocumentReminder:
    """Create the next due reminder (frozen like the invoice). Idempotent per level."""
    today = today or local_today()
    proposal = next((p for p in reminder_proposals(db_path, today=today) if p.document.id == document_id), None)
    if proposal is None:
        raise InvoicingError("Für diese Rechnung ist derzeit keine Mahnung fällig "
                             "(bezahlt, gesperrt, nicht versendet, vorherige Mahnung nicht versendet oder zu früh).")
    doc = proposal.document
    creditor = _creditor(db_path, doc.period_end)
    payee, iban = get_payment_details(db_path)
    html = render("invoices/reminder.html", {
        "doc": doc, "participant": get_participant(db_path, doc.participant_id), "level": proposal.level, "label": REMINDER_LABELS[proposal.level], "issue_date": today,
        "open_amount": doc.open_chf, "new_due_date": today + timedelta(days=10), "creditor": creditor,
        "payee": payee, "iban": format_iban(iban),
    })
    with connect(db_path) as conn:
        with conn:
            cur = conn.execute(
                "INSERT INTO document_reminders (document_id, level, issue_date, open_amount_chf, snapshot_html, "
                "snapshot_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (doc.id, proposal.level, date_to_str(today), str(doc.open_chf), html,
                 hashlib.sha256(html.encode("utf-8")).hexdigest(), now_iso()))
            record_event(conn, "billing_document", doc.id, "reminder_created",
                         f"{doc.number} {REMINDER_LABELS[proposal.level]} offen CHF {doc.open_chf}")
    return next(r for r in get_document(db_path, document_id).reminders if r.id == cur.lastrowid)


def get_reminder(db_path: Path, reminder_id: int) -> DocumentReminder:
    with connect(db_path) as conn:
        row = conn.execute("SELECT document_id FROM document_reminders WHERE id = ?", (reminder_id,)).fetchone()
    if row is None:
        raise InvoicingError(f"Mahnung {reminder_id} wurde nicht gefunden.")
    return next(r for r in get_document(db_path, row["document_id"]).reminders if r.id == reminder_id)
