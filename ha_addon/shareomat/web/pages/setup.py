# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/setup.py

Purpose:
    First-run setup wizard: Gemeinschaft → Teilnehmer → Messpunkt → Tarif.
    Shown instead of the dashboard whenever the database does not yet
    hold enough master data to build a LegConfig — see
    SHAREOMAT_UMBAU_STRUKTUR.md §8. Each step is a minimal form; the full
    management pages (Teilnehmer/Messpunkte/Tarife) take over from there.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from datetime import date

from shareomat.database.community import save_community
from shareomat.database.config_builder import get_setup_status
from shareomat.database.meters import create_meter, list_meters
from shareomat.database.participants import create_participant, list_participants
from shareomat.database.tariffs import create_tariff
from shareomat.leg_const import (
    METER_ROLE_PRODUCER_CONSUMER,
    PARTICIPANT_TYPE_PRODUCER_CONSUMER,
)
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff
from shareomat.web.rendering import FormError, RequestContext, form_decimal, form_required, render_page
from shareomat.web.state import get_state


def handle_get(ctx: RequestContext) -> str:
    """Render the setup wizard at whichever step is next incomplete."""
    status = get_setup_status(ctx.db_path)
    participants = list_participants(ctx.db_path, include_inactive=False) if status.has_community else []
    meters = list_meters(ctx.db_path, include_inactive=False) if status.has_participants else []
    return render_page(
        "setup.html", ctx, "setup",
        status=status, participants=participants, meters=meters,
    )


def handle_post(ctx: RequestContext) -> str | None:
    """Create the community, first participant, first meter, or first tariff, per wizard step."""
    step = ctx.form.get("step", "")

    if step == "community":
        community_id = form_required(ctx, "community_id", label="Gemeinschafts-ID")
        name = form_required(ctx, "name", label="Name")
        save_community(ctx.db_path, Community(community_id=community_id, name=name))
        get_state().set_flash("Gemeinschaft angelegt.", ok=True)
        return None

    if step == "participant":
        participant_id = form_required(ctx, "participant_id", label="Teilnehmer-ID")
        label = form_required(ctx, "label", label="Bezeichnung")
        try:
            create_participant(ctx.db_path, Participant(
                participant_id=participant_id, label=label,
                participant_type=PARTICIPANT_TYPE_PRODUCER_CONSUMER,
            ))
        except ValueError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Teilnehmer angelegt.", ok=True)
        return None

    if step == "meter":
        meter_id = form_required(ctx, "meter_id", label="Messpunkt-ID")
        label = form_required(ctx, "label", label="Bezeichnung")
        participant_id = form_required(ctx, "participant_id", label="Teilnehmer")
        try:
            create_meter(ctx.db_path, Meter(
                meter_id=meter_id, participant_id=participant_id, label=label,
                role=METER_ROLE_PRODUCER_CONSUMER,
            ))
        except ValueError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Messpunkt angelegt.", ok=True)
        return None

    if step == "tariff":
        create_tariff(ctx.db_path, Tariff(
            local_rate_chf_kwh=form_decimal(ctx, "local_rate_chf_kwh", label="Lokaltarif"),
            grid_rate_chf_kwh=form_decimal(ctx, "grid_rate_chf_kwh", label="Netztarif"),
            feed_in_rate_chf_kwh=form_decimal(ctx, "feed_in_rate_chf_kwh", label="Einspeisetarif"),
            valid_from=date.today(),
        ))
        get_state().set_flash("Tarif angelegt.", ok=True)
        return None

    raise FormError("Unbekannter Einrichtungsschritt.")
