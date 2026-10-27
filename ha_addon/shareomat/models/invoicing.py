# -*- coding: utf-8 -*-
"""
File: shareomat/models/invoicing.py

Purpose:
    Domain models for "Rechnungen & Gutschriften": the documents issued
    from a released billing run (one per participant), and the
    append-only logs around them — payments received, payouts made,
    deliveries and payment reminders.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    A document's rendered text is frozen at issue time (snapshot_html +
    snapshot_sha256); later edits to participants, the contract or the
    payment details never change an issued document. Corrections happen
    by cancelling (only while nothing was paid) and issuing anew.

    Statuses shown in the UI (open, overdue, paid, ...) are derived from
    the logs, never stored — only "issued" / "cancelled" is stored.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

DOC_TYPE_INVOICE = "invoice"     # Rechnung: the participant owes the LEG
DOC_TYPE_CREDIT = "credit"       # Gutschrift: the LEG owes the participant (producer)

DOC_STATUS_ISSUED = "issued"
DOC_STATUS_CANCELLED = "cancelled"

PAYMENT_KIND_PAYMENT = "payment"  # money received from a consumer (on an invoice)
PAYMENT_KIND_PAYOUT = "payout"    # money paid out to a producer (on a credit)
PAYMENT_KIND_REFUND = "refund"    # over-payment returned to the payer (on an invoice, stored negative)

DELIVERY_METHODS = {"paper": "Papier / PDF", "email": "E-Mail"}
DELIVERY_ITEMS = ("document", "reminder", "cancellation")

# Reminder levels: days after the due date from which a level is proposed.
REMINDER_OFFSETS_DAYS = (5, 20, 35)
REMINDER_LABELS = {1: "Zahlungserinnerung", 2: "1. Mahnung", 3: "2. Mahnung"}


_DOCUMENT_RECORD_FIELDS = ("billing_run_id", "participant_id", "doc_type", "number", "amount_chf", "issue_date",
                           "due_date", "recipient_label", "recipient_email", "recipient_address", "snapshot_sha256",
                           "qr_payload", "qr_reference", "qr_reference_type", "qr_render_version", "qr_iban_snapshot",
                           "qr_account_holder_snapshot", "payment_profile_version")


def record_hash(values: dict) -> str:
    """SHA-256 over the canonical financial record of a document or reminder (all values as strings).

    snapshot_sha256 protects the printed text; this protects the separately
    stored fields that later steps rely on (amount, reference, account) and
    binds them to that text.
    """
    import json
    canonical = json.dumps({k: "" if v is None else str(v) for k, v in sorted(values.items())},
                           ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def document_record(doc_values: dict) -> dict:
    return {k: doc_values.get(k) for k in _DOCUMENT_RECORD_FIELDS}


def payload_fields(qr_payload: str) -> dict:
    """The fields of a Swiss QR text Shareomat cross-checks (empty dict without payload)."""
    lines = qr_payload.split("\r\n") if qr_payload else []
    if len(lines) < 31:
        return {}
    return {"iban": lines[3], "holder": lines[5], "amount": lines[18], "reference_type": lines[27],
            "reference": lines[28]}


@dataclass
class DocumentPayment:
    """One booked payment (invoice) or payout (credit). Negative amounts are corrections."""

    document_id: int
    kind: str
    amount_chf: Decimal
    booked_on: date
    reference: str = ""
    note: str = ""
    recorded_at: datetime | None = None
    id: int | None = None


@dataclass
class DocumentDelivery:
    """One delivery attempt of a document or reminder (paper hand-over, e-mail)."""

    document_id: int
    method: str
    status: str          # "sent" | "failed"
    detail: str = ""
    item: str = "document"   # document | reminder | cancellation
    reminder_id: int | None = None
    recorded_at: datetime | None = None
    id: int | None = None


@dataclass
class DocumentReminder:
    """A payment reminder for an invoice — refers to the original claim, never a new one."""

    document_id: int
    level: int
    issue_date: date
    open_amount_chf: Decimal
    snapshot_html: str = ""
    snapshot_sha256: str = ""
    new_due_date: date | None = None
    discarded_at: datetime | None = None
    sent_at: datetime | None = None
    qr_payload: str = ""               # frozen Swiss QR text (open amount, same reference as the invoice)
    qr_reference: str = ""
    record_sha256: str = ""
    id: int | None = None

    @property
    def integrity_ok(self) -> bool:
        if hashlib.sha256(self.snapshot_html.encode("utf-8")).hexdigest() != self.snapshot_sha256:
            return False
        if not self.qr_payload:
            return True
        fields = payload_fields(self.qr_payload)
        return (fields.get("reference") == self.qr_reference
                and fields.get("amount") == f"{self.open_amount_chf:.2f}"
                and self.record_sha256 == record_hash(self.record_values()))

    def record_values(self) -> dict:
        return {"document_id": self.document_id, "level": self.level, "issue_date": self.issue_date,
                "open_amount_chf": self.open_amount_chf, "snapshot_sha256": self.snapshot_sha256,
                "qr_payload": self.qr_payload, "qr_reference": self.qr_reference, "new_due_date": self.new_due_date}

    @property
    def label(self) -> str:
        return REMINDER_LABELS.get(self.level, f"Mahnstufe {self.level}")


@dataclass
class BillingDocument:
    """An issued invoice or credit note, with its derived payment state."""

    billing_run_id: int
    participant_id: str
    doc_type: str
    number: str
    amount_chf: Decimal
    issue_date: date
    due_date: date | None
    recipient_label: str
    recipient_email: str = ""
    recipient_address: str = ""        # frozen with the document (multi-line)
    status: str = DOC_STATUS_ISSUED
    snapshot_html: str = ""
    snapshot_sha256: str = ""
    reminder_block_reason: str = ""
    cancel_reason: str = ""
    cancelled_at: datetime | None = None
    cancel_number: str = ""            # Stornobeleg, only when the document had been delivered
    cancel_snapshot_html: str = ""
    cancel_sha256: str = ""
    cancellation_sent_at: datetime | None = None
    replaces_number: str = ""
    contract_versions: str = ""
    qr_payload: str = ""               # frozen Swiss QR text, invoices only
    qr_reference: str = ""
    qr_reference_type: str = ""
    qr_iban_snapshot: str = ""
    qr_account_holder_snapshot: str = ""
    qr_render_version: str = ""
    payment_profile_version: str = ""
    record_sha256: str = ""            # record_hash() over the financial fields, see _DOCUMENT_RECORD_FIELDS
    period_start: date | None = None   # joined in from the billing run
    period_end: date | None = None
    local_kwh: Decimal = Decimal(0)    # LEG-Bezug (invoice) or LEG-Lieferung (credit) of the line item
    booked_chf: Decimal = Decimal(0)   # sum of payments (invoice) or payouts (credit)
    sent_at: datetime | None = None    # first successful delivery
    payments: list[DocumentPayment] = field(default_factory=list)
    deliveries: list[DocumentDelivery] = field(default_factory=list)
    reminders: list[DocumentReminder] = field(default_factory=list)
    id: int | None = None

    @property
    def is_invoice(self) -> bool:
        return self.doc_type == DOC_TYPE_INVOICE

    @property
    def active_reminders(self) -> list[DocumentReminder]:
        return [r for r in self.reminders if r.discarded_at is None]

    @property
    def integrity_ok(self) -> bool:
        """Frozen text, separately stored financial/QR fields and reminders are all unchanged and consistent.

        - the text matches snapshot_sha256,
        - the record (amount, recipient, QR payload/reference/IBAN/holder, ...)
          matches record_sha256, which also covers snapshot_sha256,
        - reference, IBAN, holder and amount stored separately equal those
          inside the frozen QR text,
        - Stornobeleg and every reminder pass their own checks.
        """
        ok = hashlib.sha256(self.snapshot_html.encode("utf-8")).hexdigest() == self.snapshot_sha256
        ok = ok and self.record_sha256 == record_hash(document_record(self.record_values()))
        if self.qr_payload:
            fields = payload_fields(self.qr_payload)
            ok = ok and fields == {"iban": self.qr_iban_snapshot, "holder": self.qr_account_holder_snapshot,
                                   "amount": f"{self.amount_chf:.2f}", "reference_type": self.qr_reference_type,
                                   "reference": self.qr_reference}
        if self.cancel_number:
            ok = ok and hashlib.sha256(self.cancel_snapshot_html.encode("utf-8")).hexdigest() == self.cancel_sha256
        return ok and all(r.integrity_ok for r in self.reminders)

    def record_values(self) -> dict:
        return {k: getattr(self, k) for k in _DOCUMENT_RECORD_FIELDS}

    @property
    def open_chf(self) -> Decimal:
        """Amount still to be paid (invoice) or paid out (credit); never negative."""
        if self.status == DOC_STATUS_CANCELLED:
            return Decimal(0)
        return max(self.amount_chf - self.booked_chf, Decimal(0))

    def state(self, today: date) -> str:
        """Derived status key — see STATE_LABELS."""
        if self.status == DOC_STATUS_CANCELLED:
            return "cancelled"
        if self.is_invoice:
            if self.booked_chf > self.amount_chf:
                return "overpaid"
            if self.booked_chf == self.amount_chf:
                return "paid"
            if self.due_date is not None and today > self.due_date:
                return "overdue"
            return "partially_paid" if self.booked_chf > 0 else "open"
        if self.booked_chf >= self.amount_chf:
            return "payout_done"
        return "payout_partial" if self.booked_chf > 0 else "credit_prepared"


STATE_LABELS = {
    "cancelled": "Storniert",
    "open": "Offen",
    "partially_paid": "Teilbezahlt",
    "overdue": "Überfällig",
    "paid": "Bezahlt",
    "overpaid": "Überzahlt",
    "credit_prepared": "Gutschrift erstellt",
    "payout_partial": "Teilweise ausbezahlt",
    "payout_done": "Ausbezahlt",
}


@dataclass
class PayoutStatus:
    """What may be paid out on one credit note right now (contract §3.4)."""

    document_id: int
    amount_chf: Decimal         # the credit
    entitled_chf: Decimal       # share of the consumer payments received for the same run
    paid_out_chf: Decimal
    available_chf: Decimal      # entitled - paid out, never negative


@dataclass
class ReminderProposal:
    """An overdue invoice for which the next reminder level is due."""

    document: BillingDocument
    level: int
    due_since: date

    @property
    def label(self) -> str:
        return REMINDER_LABELS.get(self.level, f"Mahnstufe {self.level}")


PROFILE_MISSING = "missing"   # nicht eingerichtet
PROFILE_CHECK = "check"       # gültig gespeichert, Scan-Test/Bankabgleich noch nicht bestätigt
PROFILE_READY = "ready"       # bestätigt — Rechnungen mit QR-Zahlteil dürfen erstellt werden
PROFILE_STATUS_LABELS = {PROFILE_MISSING: "nicht eingerichtet", PROFILE_CHECK: "prüfen", PROFILE_READY: "bereit"}


@dataclass
class PaymentProfile:
    """Bank account for consumer payments (QR bill creditor) and the invoice contact e-mail.

    payee and address must be those the bank keeps for the account — not
    necessarily the LEG's name. Only documents issued afterwards use a
    changed profile; issued documents keep their frozen QR payload.
    """

    payee: str = ""
    street: str = ""
    building: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = "CH"
    iban: str = ""
    contact_email: str = ""

    def as_dict(self) -> dict:
        return {k: getattr(self, k) for k in ("payee", "street", "building", "postal_code", "city", "country",
                                              "iban", "contact_email")}

    @property
    def version(self) -> str:
        """Hash of everything that goes into a QR bill (the confirmation is bound to it)."""
        import json
        data = {k: v for k, v in self.as_dict().items() if k != "contact_email"}
        return hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()[:16]
