# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/participants.py

Purpose:
    "Teilnehmer" admin page: list, create, edit, and activate/deactivate
    participants.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Routes (relative to /participants, dispatched via ctx.segments):
        GET  /                 list
        GET  /new               empty create form
        GET  /<id>/edit         prefilled edit form
        POST /new                create
        POST /<id>/edit          update
        POST /<id>/toggle        activate/deactivate
"""

from __future__ import annotations

from shareomat.core.participant_roles import participant_roles, roles_by_participant
from shareomat.database.meters import list_meters
from shareomat.database.participants import (
    create_participant,
    get_participant,
    list_participants,
    set_participant_active,
    update_participant,
)
from shareomat.models.participant import Participant
from shareomat.web.pages.meters import METER_ROLES
from shareomat.web.rendering import (
    FormError,
    RequestContext,
    form_bool,
    form_required,
    render_page,
)
from shareomat.web.state import get_state

def _participant_from_form(ctx: RequestContext, *, participant_id: str) -> Participant:
    return Participant(
        participant_id=participant_id,
        label=form_required(ctx, "label", label="Name"),
        email=(ctx.form.get("email") or "").strip(),
        address_line=(ctx.form.get("address_line") or "").strip(),
        postal_code=(ctx.form.get("postal_code") or "").strip(),
        city=(ctx.form.get("city") or "").strip(),
        active=form_bool(ctx, "active"),
    )


def handle_get(ctx: RequestContext) -> str:
    """Route to the participant list, new-participant form, or edit form."""
    if not ctx.segments:
        participants = list_participants(ctx.db_path)
        return render_page(
            "participants/list.html", ctx, "participants", participants=participants,
            participant_roles=roles_by_participant(list_meters(ctx.db_path)),
        )

    if ctx.segments == ["new"]:
        return render_page(
            "participants/form.html", ctx, "participants",
            participant=None, meters=[], meter_role_labels=dict(METER_ROLES),
        )

    if len(ctx.segments) == 2 and ctx.segments[1] == "edit":
        participant = get_participant(ctx.db_path, ctx.segments[0])
        if participant is None:
            return render_page("placeholders/coming_soon.html", ctx, "participants",
                                title="Teilnehmer nicht gefunden",
                                description="Die Teilnehmer-ID wurde möglicherweise geändert. Bitte öffnen Sie den Teilnehmer erneut über die Teilnehmerliste.")
        meters = [m for m in list_meters(ctx.db_path) if m.participant_id == participant.participant_id]
        return render_page(
            "participants/form.html", ctx, "participants",
            participant=participant, meters=meters, roles=participant_roles(meters),
            meter_role_labels=dict(METER_ROLES),
        )

    return render_page("placeholders/coming_soon.html", ctx, "participants",
                        title="Seite nicht gefunden",
                        description="Diese Teilnehmeradresse ist ungültig. Bitte öffnen Sie die Teilnehmerliste erneut.")


def handle_post(ctx: RequestContext) -> str | None:
    """Create, update, or toggle a participant, depending on the sub-path."""
    if ctx.segments == ["new"]:
        participant_id = form_required(ctx, "participant_id", label="Teilnehmer-ID")
        try:
            create_participant(ctx.db_path, _participant_from_form(ctx, participant_id=participant_id))
        except ValueError as exc:
            raise FormError(str(exc))
        get_state().set_flash(f"Teilnehmer '{participant_id}' angelegt.", ok=True)
        return None

    if len(ctx.segments) == 2 and ctx.segments[1] == "edit":
        original_id = ctx.segments[0]
        new_id = form_required(ctx, "participant_id", label="Teilnehmer-ID")
        try:
            update_participant(ctx.db_path, original_id, _participant_from_form(ctx, participant_id=new_id))
        except ValueError as exc:
            raise FormError(str(exc))
        get_state().set_flash(f"Teilnehmer '{new_id}' gespeichert.", ok=True)
        return None

    if len(ctx.segments) == 2 and ctx.segments[1] == "toggle":
        participant_id = ctx.segments[0]
        participant = get_participant(ctx.db_path, participant_id)
        if participant is None:
            raise FormError(f"Teilnehmer '{participant_id}' wurde nicht gefunden.")
        set_participant_active(ctx.db_path, participant_id, not participant.active)
        state = "aktiviert" if not participant.active else "deaktiviert"
        get_state().set_flash(f"Teilnehmer '{participant_id}' {state}.", ok=True)
        return None

    raise FormError("Unbekannte Aktion.")
