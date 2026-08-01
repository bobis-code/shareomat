# -*- coding: utf-8 -*-
"""
File: shareomat/models/billing_workflow.py

Purpose:
    Domain models for the interactive "Abrechnungen" (billing) workflow:
    a user-selected period, an immutable computed/released run against
    that period, its per-participant line items, and its source-file
    audit trail. Distinct from shareomat.models.billing (MatchResult /
    BillingRecord), which are the ad-hoc, per-automatic-cycle report
    types — this module is the persisted, versioned, status-tracked
    counterpart used by shareomat.database.billing.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    A BillingRun is a snapshot: once released, its BillingLineItems never
    change even if participants/meters/tariffs are edited afterward. Only
    local_amount_chf is what Shareomat actually bills; grid_amount_chf is
    informational (EBL bills the participant's grid consumption directly)
    and must never be summed into a Shareomat invoice total.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal


@dataclass
class BillingPeriod:
    """A named date range that one or more BillingRuns can be computed against."""

    period_start: date
    period_end: date
    label: str = ""
    id: int | None = None


@dataclass
class BillingLineItem:
    """One participant's snapshot line item within a BillingRun."""

    participant_id: str
    participant_label: str
    meter_ids: list[str]
    local_received_kwh: Decimal
    local_amount_chf: Decimal          # what Shareomat actually bills this participant
    grid_import_kwh: Decimal           # informational only — EBL bills this directly
    grid_amount_chf: Decimal           # informational only — never added to local_amount_chf
    local_supplied_kwh: Decimal = Decimal(0)
    grid_export_kwh: Decimal = Decimal(0)
    id: int | None = None


@dataclass
class BillingSourceFile:
    """One source meter-data file that contributed to a BillingRun, for audit purposes."""

    filename: str
    sha256: str
    origin: str  # "inbox" | "archive"
    id: int | None = None


@dataclass
class BillingRun:
    """One immutable computed (draft) or released billing snapshot for a BillingPeriod."""

    billing_period_id: int
    version: int
    status: str  # BILLING_STATUS_DRAFT | _RELEASED | _CANCELLED
    community_id: str
    community_name: str
    tariff_name: str
    local_rate_chf_kwh: Decimal
    grid_rate_chf_kwh: Decimal
    feed_in_rate_chf_kwh: Decimal
    participant_count: int
    total_local_kwh: Decimal
    total_leg_amount_chf: Decimal
    total_grid_kwh: Decimal
    total_grid_amount_chf: Decimal
    computed_at: datetime
    released_at: datetime | None = None
    period_start: date | None = None  # joined in from billing_periods for display convenience
    period_end: date | None = None
    period_label: str = ""
    id: int | None = None


@dataclass
class BillingRunDetail:
    """A BillingRun together with its line items and source files (for the detail page)."""

    run: BillingRun
    line_items: list[BillingLineItem]
    sources: list[BillingSourceFile]


@dataclass
class DataAvailability:
    """Which configured meters have/lack data for a period, and which data is unrecognized."""

    meters_with_data: list[str]
    meters_missing_data: list[str]
    unknown_meter_ids: list[str]
    reading_count: int
    sources: list[BillingSourceFile] = field(default_factory=list)


@dataclass
class BillingPreview:
    """The live (not yet saved) result of computing a billing for an arbitrary period."""

    period_start: date
    period_end: date
    availability: DataAvailability
    tariff_name: str
    local_rate_chf_kwh: Decimal
    grid_rate_chf_kwh: Decimal
    feed_in_rate_chf_kwh: Decimal
    line_items: list[BillingLineItem]

    @property
    def total_local_kwh(self) -> Decimal:
        return sum((i.local_received_kwh for i in self.line_items), Decimal(0))

    @property
    def total_leg_amount_chf(self) -> Decimal:
        return sum((i.local_amount_chf for i in self.line_items), Decimal(0))

    @property
    def total_grid_kwh(self) -> Decimal:
        return sum((i.grid_import_kwh for i in self.line_items), Decimal(0))

    @property
    def total_grid_amount_chf(self) -> Decimal:
        return sum((i.grid_amount_chf for i in self.line_items), Decimal(0))
