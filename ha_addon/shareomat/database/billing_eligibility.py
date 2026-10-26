# -*- coding: utf-8 -*-
"""
File: shareomat/database/billing_eligibility.py

Purpose:
    Decide, per local calendar day, which meters may take part in the LEG
    settlement: the meter must be valid that day, belong to an active
    participant, and — once the community works with contracts — that
    participant must be an effective member of the contract version in
    force that day.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Effective member on day d (contract mode): an assignment of the
    version in force on d with joined_at <= d, no left_at before d, a
    recorded signature (accepted_at), d >= effective_start_at when the grid
    operator confirmed one, and — for a carried member of a version
    published "with consent of all" — recorded consent (notified_at).
    The community-level start confirmed by the grid operator
    (vnb_confirmed_start) is a hard lower bound: before it the grid
    operator allocates no LEG energy at all.

    Things that make a preview unreliable but don't change who is counted
    (missing grid-operator confirmation, ...) are reported as release
    blockers by shareomat.database.billing, not silently dropped here.

    Installations without any published contract keep the previous
    behaviour (active participants and valid meters only).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from shareomat.database.community import get_community
from shareomat.database.contract_versions import get_contract_version_for_date, list_contract_versions
from shareomat.database.meters import list_meters
from shareomat.database.participant_contract import list_assignments_for_version
from shareomat.database.participants import list_participants
from shareomat.leg_const import CONTRACT_STATUS_PUBLISHED
from shareomat.models.meter import Meter


@dataclass
class DayEligibility:
    """Who may take part on one local day, and why others may not."""

    meters: dict[str, str] = field(default_factory=dict)          # meter_id -> participant_id
    excluded: dict[str, str] = field(default_factory=dict)        # meter_id -> reason
    unconfirmed_participants: set[str] = field(default_factory=set)  # counted, but grid operator hasn't confirmed


def _meter_valid(meter: Meter, day: date) -> bool:
    if not meter.active:
        return False
    if meter.valid_from is not None and day < meter.valid_from:
        return False
    if meter.valid_until is not None and day > meter.valid_until:
        return False
    return True


class EligibilityResolver:
    """Caches master data once per billing preview and answers eligibility per day."""

    def __init__(self, db_path) -> None:
        self._db_path = db_path
        self._meters = list_meters(db_path)
        self._active_participants = {p.participant_id for p in list_participants(db_path, include_inactive=False)}
        self.contract_mode = any(v.status == CONTRACT_STATUS_PUBLISHED for v in list_contract_versions(db_path))
        community = get_community(db_path)
        self.community_confirmed_start = community.vnb_confirmed_start if community else None
        self._cache: dict[date, DayEligibility] = {}
        self._assignments_by_version: dict[int, list] = {}

    def for_day(self, day: date) -> DayEligibility:
        cached = self._cache.get(day)
        if cached is not None:
            return cached
        result = DayEligibility()
        members: dict[str, object] | None = None
        version = None
        if self.contract_mode:
            version = get_contract_version_for_date(self._db_path, day)
            members = {}
            if version is not None:
                rows = self._assignments_by_version.get(version.id)
                if rows is None:
                    rows = list_assignments_for_version(self._db_path, version.id)
                    self._assignments_by_version[version.id] = rows
                members = {a.participant_id: a for a in rows}

        for meter in self._meters:
            if not meter.participant_id or meter.role == "grid":
                continue
            if not _meter_valid(meter, day):
                continue
            pid = meter.participant_id
            if pid not in self._active_participants:
                result.excluded[meter.meter_id] = "Teilnehmer inaktiv"
                continue
            if self.contract_mode:
                reason = self._membership_problem(day, version, members.get(pid))
                if reason:
                    result.excluded[meter.meter_id] = reason
                    continue
                if members[pid].vnb_confirmed_at is None:
                    result.unconfirmed_participants.add(pid)
            result.meters[meter.meter_id] = pid
        self._cache[day] = result
        return result

    def _membership_problem(self, day: date, version, assignment) -> str:
        if version is None:
            return "kein Vertrag in Kraft"
        if self.community_confirmed_start is not None and day < self.community_confirmed_start:
            return "vor dem vom Netzbetreiber bestätigten LEG-Start"
        if assignment is None:
            return f"nicht Mitglied von Vertrag v{version.version}"
        if assignment.accepted_at is None:
            return "Beitritt nicht unterschrieben"
        if assignment.joined_at is not None and day < assignment.joined_at:
            return "vor dem Beitritt"
        if assignment.left_at is not None and day > assignment.left_at:
            return "nach dem Austritt"
        if assignment.effective_start_at is not None and day < assignment.effective_start_at:
            return "vor dem vom Netzbetreiber bestätigten Beginn"
        if version.consent_based and assignment.accepted_version not in (None, version.version) \
                and assignment.notified_at is None:
            return f"Zustimmung zu v{version.version} fehlt"
        return ""
