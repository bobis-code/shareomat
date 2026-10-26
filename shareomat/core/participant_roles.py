# -*- coding: utf-8 -*-
"""
File: shareomat/core/participant_roles.py

Purpose:
    Derive what a participant does in the LEG (beziehen, liefern,
    Speicher) from the roles of its meters.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    A participant has no type of its own: one participant can own several
    meters (e.g. a Bezugs- and a PV-Messpunkt), and each meter carries its
    own role. Only active meters count; grid meters never belong to a
    participant's LEG role.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from shareomat.leg_const import (
    METER_ROLE_CONSUMER,
    METER_ROLE_PRODUCER,
    METER_ROLE_PRODUCER_CONSUMER,
    METER_ROLE_STORAGE,
)
from shareomat.models.meter import Meter

_PRODUCING_ROLES = {METER_ROLE_PRODUCER, METER_ROLE_PRODUCER_CONSUMER, METER_ROLE_STORAGE}
_CONSUMING_ROLES = {METER_ROLE_CONSUMER, METER_ROLE_PRODUCER_CONSUMER, METER_ROLE_STORAGE}


@dataclass(frozen=True)
class ParticipantRoles:
    """What one participant does in the LEG, derived from its active meters."""

    meter_ids: tuple[str, ...] = ()
    produces: bool = False
    consumes: bool = False
    storage: bool = False

    @property
    def label(self) -> str:
        if self.storage:
            return "Speicherbetreiber"
        if self.produces and self.consumes:
            return "Erzeuger & Verbraucher"
        if self.produces:
            return "Erzeuger"
        if self.consumes:
            return "Verbraucher"
        return "kein Messpunkt"


def participant_roles(meters: Iterable[Meter]) -> ParticipantRoles:
    """Combine the roles of one participant's meters (inactive meters are ignored)."""
    active = [m for m in meters if m.active and (m.role in _PRODUCING_ROLES or m.role in _CONSUMING_ROLES)]
    return ParticipantRoles(
        meter_ids=tuple(m.meter_id for m in active),
        produces=any(m.role in _PRODUCING_ROLES for m in active),
        consumes=any(m.role in _CONSUMING_ROLES for m in active),
        storage=any(m.role == METER_ROLE_STORAGE for m in active),
    )


def roles_by_participant(meters: Iterable[Meter]) -> dict[str, ParticipantRoles]:
    """Group meters by participant_id and derive each participant's roles."""
    grouped: dict[str, list[Meter]] = {}
    for meter in meters:
        if meter.participant_id:
            grouped.setdefault(meter.participant_id, []).append(meter)
    return {pid: participant_roles(ms) for pid, ms in grouped.items()}
