# -*- coding: utf-8 -*-
"""
File: shareomat/models/contract.py

Purpose:
    Domain model for the LEG contract master data ("Vertrag" admin page):
    LEG representative, grid operator, and distribution method used to
    prefill a brand-new first draft.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container — no business logic, no I/O. ContractSettings is
    persisted through the existing generic settings table (see
    shareomat.database.contract_settings), same pattern as
    shareomat.models.external_data.ExternalDataSettings — no dedicated SQL
    table for this single-row-per-community prefill data.

    The legally binding data — prices, notice periods, representative,
    grid operator, distribution method at the time a version was published
    — lives entirely on ContractVersion, never on ContractSettings.
    ContractSettings only supplies default values when starting a brand
    new draft from scratch (see shareomat.web.pages.contract).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal


@dataclass
class ContractSettings:
    """Prefill defaults for a brand-new contract draft — not itself legally binding."""

    representative_name: str = ""
    representative_address_line: str = ""
    representative_postal_code: str = ""
    representative_city: str = ""
    representative_email: str = ""
    grid_operator: str = ""
    distribution_method: str = "proportional zum Verbrauch"  # LEG-Mustervertrag §1


@dataclass
class ContractVersion:
    """One versioned snapshot of the LEG contract's terms and rendered text.

    Only two stored statuses: "draft" (freely editable via
    update_draft_version) and "published" (immutable — a change always
    creates a new draft version instead, see
    shareomat.database.contract_versions). Whether a published version is
    currently in force, merely announced, or historical is derived from
    valid_from/valid_until at read time, never a third stored status.
    """

    community_id: str
    version: int
    status: str
    contract_text_snapshot: str

    # Pricing (source for the tariff created at publish time).
    local_rate_chf_kwh: Decimal = Decimal("0")
    local_rate_nt_chf_kwh: Decimal | None = None
    feed_in_rate_chf_kwh: Decimal = Decimal("0")
    feed_in_rate_nt_chf_kwh: Decimal | None = None
    admin_fee_chf_kwh: Decimal = Decimal("0")
    rate_mode: str = "flat"

    # Master-data snapshot (diffed at publish time to pick the required notice period).
    representative_name: str = ""
    representative_address_line: str = ""
    representative_postal_code: str = ""
    representative_city: str = ""
    representative_email: str = ""
    grid_operator: str = ""
    distribution_method: str = "proportional zum Verbrauch"

    # Notice periods are contract parameters, not Python constants — see
    # LEG-Mustervertrag §3 (price changes) and §8 (contract amendments).
    price_notice_period_months: int = 4
    contract_notice_period_months: int = 6

    valid_from: date | None = None
    valid_until: date | None = None

    # Traceability, set only by publish_version()/left NULL on drafts —
    # lets withdraw_version() undo exactly what publishing did.
    tariff_id: int | None = None
    supersedes_version_id: int | None = None

    created_at: datetime | None = None
    updated_at: datetime | None = None
    id: int | None = None
