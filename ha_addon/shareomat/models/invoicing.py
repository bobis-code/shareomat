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
    id: int | None = None

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
        """The stored text still matches the checksum taken when it was frozen."""
        ok = hashlib.sha256(self.snapshot_html.encode("utf-8")).hexdigest() == self.snapshot_sha256
        if self.cancel_number:
            ok = ok and hashlib.sha256(self.cancel_snapshot_html.encode("utf-8")).hexdigest() == self.cancel_sha256
        return ok and all(hashlib.sha256(r.snapshot_html.encode("utf-8")).hexdigest() == r.snapshot_sha256
                          for r in self.reminders)

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
