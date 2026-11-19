# -*- coding: utf-8 -*-
"""
File: shareomat/database/participant_documents.py

Purpose:
    Read-only assembly of the documents printed for a participant under
    "Dokumente". For now: the notice to the grid operator that a new
    participant joins the existing LEG, with exactly the fields of EBL's
    form "Teilnahmeformular neue LEG-Teilnehmende".

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Shareomat prints the content of that form in its own neutral layout,
    never EBL's logo or letterhead (the original is EBL's document and is
    not kept in this public repository).
    Needs verification: whether EBL accepts the notice in this layout or
    insists on its own form; until then the operator can also copy the
    values into EBL's form by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from shareomat.database.community import get_community
from shareomat.database.contract_settings import get_contract_settings
from shareomat.database.memberships import list_memberships
from shareomat.database.meters import list_meters
from shareomat.database.participants import get_participant, list_participants
from shareomat.leg_const import (
    METER_ROLE_GRID,
    METER_ROLE_PRODUCER,
    METER_ROLE_PRODUCER_CONSUMER,
    METER_ROLE_STORAGE,
)

_GENERATING_ROLES = {METER_ROLE_PRODUCER, METER_ROLE_PRODUCER_CONSUMER, METER_ROLE_STORAGE}


@dataclass
class EblJoinNotice:
    """The fields of EBL's form "Bestätigung Eintritt in LEG durch neuen Teilnehmenden", filled in."""

    participant_id: str
    community_name: str
    representative: str
    participant_name: str
    object_address: str
    meter_ids: list[str]
    generation_output: str        # "9.8 kWp", several meters listed with their id, "" = no plant
    connection_power: str
    email: str
    phone: str
    start: date | None            # joined_at of the open membership
    missing: list[str] = field(default_factory=list)   # needed for the form
    hints: list[str] = field(default_factory=list)     # optional fields left empty (not an error)

    @property
    def complete(self) -> bool:
        return not self.missing


# ── Private helpers ─────────────────────────────────────────────────────────


def _kwp(value: Decimal) -> str:
    return f"{value.normalize():f} kWp"


def _per_meter(values: list[tuple[str, str]]) -> str:
    """One value as it is; several with the end of their meter id, e.g. "…T01: 9.8 kWp"."""
    if len(values) == 1:
        return values[0][1]
    return ", ".join(f"…{meter_id[-4:]}: {value}" for meter_id, value in values)


def _open_join_date(db_path: Path, participant_id: str) -> date | None:
    open_memberships = [m for m in list_memberships(db_path, participant_id) if m.left_at is None and m.joined_at]
    return max((m.joined_at for m in open_memberships), default=None)


# ── Public functions ────────────────────────────────────────────────────────


def ebl_join_notice(db_path: Path, participant_id: str) -> EblJoinNotice | None:
    """The filled-in join notice of one participant, None for an unknown participant."""
    participant = get_participant(db_path, participant_id)
    if participant is None:
        return None
    community = get_community(db_path)
    representative = get_contract_settings(db_path).representative_name
    meters = [m for m in list_meters(db_path, include_inactive=False)
              if m.participant_id == participant_id and m.role != METER_ROLE_GRID]
    generating = [m for m in meters if m.role in _GENERATING_ROLES]
    town = f"{participant.postal_code} {participant.city}".strip()
    address = ", ".join(part for part in (participant.address_line, town) if part)

    kwp = [(m.meter_id, _kwp(m.generation_kwp)) for m in generating if m.generation_kwp is not None]
    connection = [(m.meter_id, m.connection_power) for m in meters if m.connection_power]
    notice = EblJoinNotice(
        participant_id=participant_id,
        community_name=community.name if community else "",
        representative=representative,
        participant_name=participant.label,
        object_address=address,
        meter_ids=[m.meter_id for m in meters],
        generation_output=_per_meter(kwp) if kwp else "",
        connection_power=_per_meter(connection) if connection else "",
        email=participant.email,
        phone=participant.phone,
        start=_open_join_date(db_path, participant_id),
    )
    checks = (
        (notice.community_name, "Name der LEG (Gemeinschaft)"),
        (notice.representative, "LEG-Vertreter (Vertrag)"),
        (participant.address_line and participant.postal_code and participant.city, "Objektadresse"),
        (notice.meter_ids, "aktiver Messpunkt"),
        (len(kwp) == len(generating), "Leistung Erzeugungsanlage (kWp) beim Messpunkt"),
        (notice.email, "E-Mail"),
        (notice.phone, "Telefon"),
        (notice.start, "Beitritt per (Mitgliedschaft)"),
    )
    notice.missing = [label for value, label in checks if not value]
    # Optional (operator's decision 19.11.2026): left empty on the form if not entered.
    if meters and len(connection) < len(meters):
        notice.hints.append("Anschlussleistung nicht angegeben (freiwillig)")
    return notice


def ebl_join_notices(db_path: Path) -> list[EblJoinNotice]:
    """The join notice of every active participant, by name."""
    notices = (ebl_join_notice(db_path, p.participant_id) for p in list_participants(db_path, include_inactive=False))
    return [n for n in notices if n is not None]
