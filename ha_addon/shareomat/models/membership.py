# -*- coding: utf-8 -*-
"""
File: shareomat/models/membership.py

Purpose:
    Domain model of one LEG membership: a participant's period in the
    community (join, departure) and what the grid operator was told and
    confirmed about it. Independent of contract versions (ADR-0009).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container — no business logic, no I/O. A participant who
    leaves and later rejoins has two memberships that never overlap.
    Signatures, declarations, notices and consents belong to a contract
    version and live on shareomat.database.participant_contract rows,
    each linked to exactly one membership.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime


@dataclass
class Membership:
    """One participant's membership period with the grid operator's confirmation."""

    participant_id: str            # business key (Participant.participant_id)
    joined_at: date | None
    left_at: date | None = None
    exit_reason: str = ""
    vnb_notified_at: date | None = None
    vnb_confirmed_at: date | None = None
    effective_start_at: date | None = None
    vnb_notified_paperless_id: int | None = None
    vnb_notified_paperless_asn: int | None = None
    vnb_confirmed_paperless_id: int | None = None
    vnb_confirmed_paperless_asn: int | None = None
    # Set by the schema-18 migration when the old per-version rows disagreed;
    # billing is blocked until the operator confirms the values.
    review_note: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    id: int | None = None

    def covers(self, day: date) -> bool:
        """True if `day` lies between join and departure (either end open)."""
        if self.joined_at is not None and day < self.joined_at:
            return False
        if self.left_at is not None and day > self.left_at:
            return False
        return True
