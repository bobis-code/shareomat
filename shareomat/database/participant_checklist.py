# -*- coding: utf-8 -*-
"""
File: shareomat/database/participant_checklist.py

Purpose:
    The participant checklist ("VERWALTUNG → Checkliste"): per participant
    what is done, what is missing, what waits for the grid operator, and
    whether the participant is billable in a chosen month; plus the
    community-wide points ("LEG gesamt") with deadline warnings.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    * Read-only. Nothing is stored for the checklist itself — every state
      is derived from the data the existing functions write, so the list
      can never disagree with the data.
    * "Abrechnung" comes from shareomat.database.billing_eligibility
      (the very function billing uses), never from a second rule set.
    * Three separate areas: administratively complete (documents and
      reports), billable in a period (EligibilityResolver), technically
      connected (meter data). Missing meter data never keeps an
      administrative point open.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from shareomat.core.pipeline.raw.sdat_ch import is_valid_eic
from shareomat.database.billing_eligibility import EligibilityResolver
from shareomat.database.community import get_community
from shareomat.database.contract_versions import list_contract_versions
from shareomat.database.invoicing import payment_profile_status
from shareomat.database.memberships import list_memberships
from shareomat.database.meters import list_meters
from shareomat.database.participant_contract import load_assignments
from shareomat.database.participants import list_participants
from shareomat.database.sqlite import connect
from shareomat.database.tariffs import get_tariff_for_date
from shareomat.leg_const import CONTRACT_STATUS_PUBLISHED

DONE, OPEN, WAITING, NOT_NEEDED, CHECK = "done", "open", "waiting", "na", "check"
STATE_LABELS = {DONE: "erledigt", OPEN: "offen", WAITING: "wartet", NOT_NEEDED: "nicht nötig", CHECK: "prüfen"}
STATE_SYMBOLS = {DONE: "✓", OPEN: "✗", WAITING: "⏳", NOT_NEEDED: "–", CHECK: "⚠"}
COLUMNS = (
    ("master", "Stammdaten"),
    ("meters", "Messpunkt"),
    ("contract", "Vertrag & Unterschrift"),
    ("vnb", "EBL"),
    ("notice", "Mitteilung / Zustimmung"),
    ("billing", "Abrechnung"),
)
METERING_POINT_ID = re.compile(r"^CH[0-9A-Z]{31}$")   # 33-character Swiss metering point ID (SDAT-CH)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_ROLES = {"producer", "consumer", "producer_consumer", "storage"}
_DEADLINE_DAYS = 180


@dataclass
class Point:
    """One state with the concrete text ("fehlt: E-Mail") and an anchor for the matching form."""

    state: str
    text: str = ""
    anchor: str = ""

    @property
    def symbol(self) -> str:
        return STATE_SYMBOLS[self.state]

    @property
    def label(self) -> str:
        return STATE_LABELS[self.state]


@dataclass
class ParticipantRow:
    participant: object
    membership: object | None
    assignments: list
    meters: list
    points: dict[str, Point]
    open_items: list[str] = field(default_factory=list)
    departed: bool = False

    @property
    def admin_complete(self) -> bool:
        return all(self.points[k].state in (DONE, NOT_NEEDED) for k in ("master", "meters", "contract", "vnb", "notice"))

    @property
    def needs_check(self) -> bool:
        return any(p.state == CHECK for p in self.points.values())

    @property
    def waiting(self) -> bool:
        return any(p.state == WAITING for k, p in self.points.items() if k != "billing")


@dataclass
class OpenVersionTask:
    """A published version with open notices/consents — the entry point for the collective action."""

    version: object
    kind: str            # "notice" | "consent"
    assignments: list


@dataclass
class Checklist:
    community_points: list[tuple[str, Point]]
    rows: list[ParticipantRow]
    month_start: date
    month_end: date
    version_tasks: list[OpenVersionTask]
    counts: dict[str, int]


# ── Private helpers ─────────────────────────────────────────────────────────


def _month_bounds(month: date) -> tuple[date, date]:
    first = month.replace(day=1)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first, last


def _fmt(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else "—"


def _relevant_versions(versions, today: date) -> list:
    """Published versions in force today or announced for later — what members deal with now."""
    result = []
    for v in versions:
        if v.status != CONTRACT_STATUS_PUBLISHED or v.valid_from is None:
            continue
        if v.valid_until is not None and v.valid_until < today:
            continue
        result.append(v)
    return sorted(result, key=lambda v: v.valid_from)


def _current_membership(memberships: list, today: date):
    """The membership running today or later; else the most recent one (departed)."""
    running = [m for m in memberships if m.left_at is None or m.left_at >= today]
    if running:
        return max(running, key=lambda m: (m.joined_at or date.min, m.id)), False
    if memberships:
        return max(memberships, key=lambda m: (m.joined_at or date.min, m.id)), True
    return None, False


def _master_point(p) -> Point:
    missing = [label for label, value in (("Name", p.label), ("Adresse", p.address_line), ("PLZ", p.postal_code),
                                          ("Ort", p.city)) if not (value or "").strip()]
    email = (p.email or "").strip()
    if not email:
        missing.append("E-Mail")
    if missing:
        return Point(OPEN, "fehlt: " + ", ".join(missing), "stammdaten")
    if not _EMAIL.match(email):
        return Point(CHECK, "E-Mail-Adresse ungültig", "stammdaten")
    return Point(DONE, anchor="stammdaten")


def _meter_point(meters: list, today: date) -> Point:
    usable = [m for m in meters if m.active and m.role in _ROLES and (m.valid_until is None or m.valid_until >= today)]
    if not usable:
        return Point(OPEN, "kein aktiver Messpunkt mit Rolle", "messpunkte")
    wrong = [m.meter_id for m in usable if not METERING_POINT_ID.match(m.meter_id)]
    if wrong:
        return Point(CHECK, "ID nicht im EBL-Format (33 Zeichen «CH…»): " + ", ".join(wrong), "messpunkte")
    return Point(DONE, ", ".join(m.meter_id[-6:] for m in usable), "messpunkte")


def _contract_point(membership, rows: list, relevant_ids: set[int]) -> Point:
    if membership is None or not rows:
        return Point(OPEN, "keinem Vertrag zugeordnet", "vertrag")
    current = [a for a in rows if a.contract_version_id in relevant_ids] or rows
    newest = max(current, key=lambda a: a.version_number or 0)
    if newest.accepted_at is None:
        issued = any(a.declaration_issued_at for a in rows)
        return Point(OPEN, "Unterschrift fehlt" if issued else "Erklärung ausstellen, Unterschrift fehlt", "unterschrift")
    if not newest.signature_evidence:
        return Point(OPEN, "Nachweis zur Unterschrift fehlt", "unterschrift")
    return Point(DONE, f"v{newest.signature_version}, {_fmt(newest.accepted_at)}", "unterschrift")


def _vnb_point(membership) -> Point:
    if membership is None:
        return Point(NOT_NEEDED)
    if membership.effective_start_at and membership.joined_at and membership.effective_start_at < membership.joined_at:
        return Point(CHECK, "wirksamer Beginn liegt vor dem Beitritt", "ebl")
    if membership.vnb_confirmed_at:
        return Point(DONE, f"wirksam ab {_fmt(membership.effective_start_at)}", "ebl")
    if membership.vnb_notified_at:
        return Point(WAITING, f"gemeldet {_fmt(membership.vnb_notified_at)}, Bestätigung fehlt", "ebl")
    return Point(OPEN, "bei EBL melden", "ebl")


def _needs_notice(assignment, membership_rows: list) -> bool:
    """A member carried into a version they did not sign themselves (not their first version)."""
    if assignment.accepted_at is not None and assignment.signature_version == assignment.version_number:
        return False
    earlier = [a for a in membership_rows if (a.version_number or 0) < (assignment.version_number or 0)]
    return bool(earlier)


def _notice_point(rows: list, relevant: dict[int, object], tasks: dict) -> Point:
    texts, state = [], NOT_NEEDED
    for a in sorted(rows, key=lambda r: r.version_number or 0):
        version = relevant.get(a.contract_version_id)
        if version is None or not _needs_notice(a, rows):
            continue
        if version.consent_based:
            if a.consent_at is None:
                texts.append(f"Zustimmung zu v{version.version} fehlt")
                state = OPEN
                tasks.setdefault((version.id, "consent"), []).append(a)
            elif state == NOT_NEEDED:
                state = DONE
        if a.notified_at is None:
            texts.append(f"Mitteilung v{version.version} erfassen")
            state = OPEN
            tasks.setdefault((version.id, "notice"), []).append(a)
        elif state == NOT_NEEDED:
            state = DONE
    return Point(state, "; ".join(texts), "mitteilung")


def _billing_point(participant_id: str, meters: list, resolver: EligibilityResolver, first: date, last: date) -> Point:
    meter_ids = [m.meter_id for m in meters if m.role != "grid"]
    if not meter_ids:
        return Point(NOT_NEEDED, "kein Messpunkt")
    billable_days, first_billable, first_reason, unconfirmed = 0, None, "", False
    day, total = first, 0
    while day <= last:
        total += 1
        e = resolver.for_day(day)
        if any(mid in e.meters for mid in meter_ids):
            billable_days += 1
            first_billable = first_billable or day
            unconfirmed = unconfirmed or participant_id in e.unconfirmed_participants
        elif not first_reason:
            first_reason = next((e.excluded[mid] for mid in meter_ids if mid in e.excluded), "Messpunkt nicht gültig")
        day += timedelta(days=1)
    note = " (EBL-Bestätigung fehlt: Prüfhinweis in der Abrechnung)" if unconfirmed else ""
    if billable_days == total:
        return Point(WAITING if unconfirmed else DONE, "bereit" + note, "abrechnung")
    if billable_days:
        return Point(WAITING, f"teilweise, ab {_fmt(first_billable)}{note}", "abrechnung")
    return Point(OPEN, f"nicht bereit: {first_reason}", "abrechnung")


def _community_points(db_path: Path, versions, meters, today: date, swisseldex) -> list[tuple[str, Point]]:
    community = get_community(db_path)
    points = []
    if community is None:
        return [("Gemeinschaft", Point(OPEN, "unter «Gemeinschaft» erfassen"))]
    missing = [label for label, value in (("Name", community.name), ("Adresse", community.address_line),
                                          ("PLZ", community.postal_code), ("Ort", community.city)) if not value]
    points.append(("Gemeinschaft", Point(OPEN, "fehlt: " + ", ".join(missing)) if missing else Point(DONE, community.name)))
    if not community.eic_code:
        points.append(("EIC des LEG-Vertreters", Point(OPEN, "unter «Gemeinschaft» eintragen")))
    elif not is_valid_eic(community.eic_code):
        points.append(("EIC des LEG-Vertreters", Point(CHECK, "Prüfziffer ungültig")))
    else:
        points.append(("EIC des LEG-Vertreters", Point(DONE, community.eic_code)))
    points.append(("LEG-Start vom Netzbetreiber bestätigt",
                   Point(DONE, _fmt(community.vnb_confirmed_start)) if community.vnb_confirmed_start
                   else Point(OPEN, "Datum unter «Gemeinschaft» eintragen")))
    relevant = _relevant_versions(versions, today)
    if not relevant:
        points.append(("Vertrag", Point(OPEN, "keine gültige oder angekündigte Version")))
    else:
        points.append(("Vertrag", Point(DONE, ", ".join(f"v{v.version} ab {_fmt(v.valid_from)}" for v in relevant))))
        last = relevant[-1]
        if last.valid_until is not None:
            points.append((f"Vertragsende v{last.version}", Point(
                CHECK,
                f"endet am {_fmt(last.valid_until)} — danach wird ohne Folgeversion nicht mehr abgerechnet. "
                "Der veröffentlichte Vertragstext (§5) gilt auf unbestimmte Zeit; das Enddatum in Shareomat "
                "beendet nur die Abrechnung. Folgeversion rechtzeitig veröffentlichen (Ankündigungsfrist "
                f"{last.price_notice_period_months}/{last.contract_notice_period_months} Monate, ohne Frist nur "
                "«mit Zustimmung aller»).",
            )))
    tariff = get_tariff_for_date(db_path, today)
    points.append(("LEG-Preis", Point(DONE, f"gültig ab {_fmt(tariff.valid_from)}") if tariff
                   else Point(OPEN, "kein Preis für heute (Vertrag veröffentlichen)")))
    status = payment_profile_status(db_path)
    points.append(("Zahlungsangaben", {"ready": Point(DONE, "bestätigt"),
                                       "check": Point(CHECK, "unter «Rechnungen → Einstellungen» bestätigen")}
                   .get(status, Point(OPEN, "unter «Rechnungen → Einstellungen» erfassen"))))
    if swisseldex is not None:
        snap = swisseldex.snapshot(run_limit=1, file_limit=1)
        if not snap["configured"]:
            points.append(("Swisseldex", Point(OPEN, "Zugangsdaten in den Add-on-Optionen fehlen")))
        elif snap["last_connection_ok"]:
            points.append(("Swisseldex", Point(DONE, "letzte Verbindung erfolgreich")))
        else:
            points.append(("Swisseldex", Point(CHECK, "letzte Verbindung fehlgeschlagen oder noch keine")))
    soon = today + timedelta(days=_DEADLINE_DAYS)
    for m in sorted(meters, key=lambda m: m.meter_id):
        if m.active and m.valid_until is not None and today <= m.valid_until <= soon:
            points.append((f"Messpunkt …{m.meter_id[-6:]}", Point(CHECK, f"gilt nur bis {_fmt(m.valid_until)}")))
    return points


# ── Public functions ────────────────────────────────────────────────────────


def build_checklist(db_path: Path, *, month: date | None = None, today: date | None = None,
                    swisseldex=None) -> Checklist:
    """Everything the checklist page shows, derived from the stored data (nothing is written)."""
    today = today or date.today()
    first, last = _month_bounds(month or today)
    versions = list_contract_versions(db_path)
    relevant = {v.id: v for v in _relevant_versions(versions, today)}
    meters = list_meters(db_path)
    memberships_by_participant: dict[str, list] = {}
    for m in list_memberships(db_path):
        memberships_by_participant.setdefault(m.participant_id, []).append(m)
    with connect(db_path) as conn:
        assignments = load_assignments(conn, "1 = 1", ())
    rows_by_membership: dict[int, list] = {}
    for a in assignments:
        rows_by_membership.setdefault(a.membership_id, []).append(a)
    resolver = EligibilityResolver(db_path)

    rows, tasks = [], {}
    for p in list_participants(db_path):
        own_meters = [m for m in meters if m.participant_id == p.participant_id]
        membership, departed = _current_membership(memberships_by_participant.get(p.participant_id, []), today)
        member_rows = rows_by_membership.get(membership.id, []) if membership else []
        points = {
            "master": _master_point(p),
            "meters": _meter_point(own_meters, today),
            "contract": _contract_point(membership, member_rows, set(relevant)),
            "vnb": _vnb_point(membership),
            "notice": _notice_point(member_rows, relevant, tasks) if not departed else Point(NOT_NEEDED),
            "billing": _billing_point(p.participant_id, own_meters, resolver, first, last),
        }
        if not p.active:
            points["billing"] = Point(NOT_NEEDED, "Teilnehmer inaktiv")
        if membership is not None and membership.review_note:
            points["vnb"] = Point(CHECK, "Daten aus früheren Versionen prüfen", "pruefen")
        if departed:
            for key in ("contract", "vnb"):
                if points[key].state in (OPEN, WAITING):
                    points[key] = Point(NOT_NEEDED, "ausgetreten")
        open_items = [f"{label}: {points[key].text or points[key].label}" for key, label in COLUMNS
                      if points[key].state in (OPEN, CHECK) and key != "billing"]
        rows.append(ParticipantRow(participant=p, membership=membership, assignments=member_rows,
                                   meters=own_meters, points=points, open_items=open_items, departed=departed))

    version_tasks = [OpenVersionTask(version=relevant[vid], kind=kind, assignments=items)
                     for (vid, kind), items in sorted(tasks.items(), key=lambda kv: (relevant[kv[0][0]].version, kv[0][1]))]
    active_rows = [r for r in rows if not r.departed]
    counts = {
        "all": len(rows),
        "open": sum(1 for r in active_rows if not r.admin_complete),
        "waiting": sum(1 for r in active_rows if r.waiting),
        "check": sum(1 for r in rows if r.needs_check),
        "complete": sum(1 for r in active_rows if r.admin_complete),
        "departed": sum(1 for r in rows if r.departed),
    }
    return Checklist(community_points=_community_points(db_path, versions, meters, today, swisseldex),
                     rows=rows, month_start=first, month_end=last, version_tasks=version_tasks, counts=counts)
