# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/meters.py

Purpose:
    "Messpunkte" admin page: list, create, edit (incl. participant
    assignment), and activate/deactivate meters.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from shareomat.database.meters import (
    create_meter,
    get_meter,
    list_meters,
    set_meter_active,
    update_meter,
)
from shareomat.database.participants import list_participants
from shareomat.leg_const import (
    METER_ROLE_CONSUMER,
    METER_ROLE_GRID,
    METER_ROLE_PRODUCER,
    METER_ROLE_PRODUCER_CONSUMER,
)
from shareomat.models.meter import Meter
from shareomat.web.rendering import (
    FormError,
    RequestContext,
    form_bool,
    form_date,
    form_required,
    render_page,
)
from shareomat.web.state import get_state

METER_ROLES = [
    (METER_ROLE_PRODUCER, "Erzeuger"),
    (METER_ROLE_CONSUMER, "Verbraucher"),
    (METER_ROLE_PRODUCER_CONSUMER, "Erzeuger & Verbraucher"),
    (METER_ROLE_GRID, "Netz"),
]


def _meter_from_form(ctx: RequestContext, *, meter_id: str) -> Meter:
    return Meter(
        meter_id=meter_id,
        participant_id=form_required(ctx, "participant_id", label="Teilnehmer"),
        label=form_required(ctx, "label", label="Bezeichnung"),
        role=form_required(ctx, "role", label="Rolle"),
        valid_from=form_date(ctx, "valid_from"),
        valid_until=form_date(ctx, "valid_until"),
        active=form_bool(ctx, "active"),
    )


def handle_get(ctx: RequestContext) -> str:
    """Route to the meter list, new-meter form, or edit form."""
    if not ctx.segments:
        meters = list_meters(ctx.db_path)
        return render_page("meters/list.html", ctx, "meters", meters=meters)

    participants = list_participants(ctx.db_path, include_inactive=False)

    if ctx.segments == ["new"]:
        if not participants:
            return render_page(
                "placeholders/coming_soon.html", ctx, "meters",
                title="Zuerst einen Teilnehmer anlegen",
                description="Ein Messpunkt muss einem Teilnehmer zugeordnet werden. "
                             "Legen Sie zuerst unter 'Teilnehmer' mindestens einen aktiven Teilnehmer an.",
            )
        return render_page(
            "meters/form.html", ctx, "meters",
            meter=None, participants=participants, meter_roles=METER_ROLES,
        )

    if len(ctx.segments) == 2 and ctx.segments[1] == "edit":
        meter = get_meter(ctx.db_path, ctx.segments[0])
        if meter is None:
            return render_page("placeholders/coming_soon.html", ctx, "meters",
                                title="Messpunkt nicht gefunden", description="")
        return render_page(
            "meters/form.html", ctx, "meters",
            meter=meter, participants=participants, meter_roles=METER_ROLES,
        )

    return render_page("placeholders/coming_soon.html", ctx, "meters",
                        title="Seite nicht gefunden", description="")


def handle_post(ctx: RequestContext) -> str | None:
    """Create, update, or toggle a meter, depending on the sub-path."""
    if ctx.segments == ["new"]:
        meter_id = form_required(ctx, "meter_id", label="Messpunkt-ID")
        try:
            create_meter(ctx.db_path, _meter_from_form(ctx, meter_id=meter_id))
        except ValueError as exc:
            raise FormError(str(exc))
        get_state().set_flash(f"Messpunkt '{meter_id}' angelegt.", ok=True)
        return None

    if len(ctx.segments) == 2 and ctx.segments[1] == "edit":
        original_id = ctx.segments[0]
        new_id = form_required(ctx, "meter_id", label="Messpunkt-ID")
        try:
            update_meter(ctx.db_path, original_id, _meter_from_form(ctx, meter_id=new_id))
        except ValueError as exc:
            raise FormError(str(exc))
        get_state().set_flash(f"Messpunkt '{new_id}' gespeichert.", ok=True)
        return None

    if len(ctx.segments) == 2 and ctx.segments[1] == "toggle":
        meter_id = ctx.segments[0]
        meter = get_meter(ctx.db_path, meter_id)
        if meter is None:
            raise FormError(f"Messpunkt '{meter_id}' wurde nicht gefunden.")
        set_meter_active(ctx.db_path, meter_id, not meter.active)
        state = "aktiviert" if not meter.active else "deaktiviert"
        get_state().set_flash(f"Messpunkt '{meter_id}' {state}.", ok=True)
        return None

    raise FormError("Unbekannte Aktion.")
