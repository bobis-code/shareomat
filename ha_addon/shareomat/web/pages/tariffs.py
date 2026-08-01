# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/tariffs.py

Purpose:
    "Tarife" admin page: list, create, edit, and activate/deactivate
    tariff versions (each valid for a date range).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from datetime import date

from shareomat.database.tariffs import (
    create_tariff,
    get_tariff,
    get_tariff_for_date,
    list_tariffs,
    set_tariff_active,
    update_tariff,
)
from shareomat.models.tariff import Tariff
from shareomat.web.rendering import (
    FormError,
    RequestContext,
    form_bool,
    form_date,
    form_decimal,
    form_required,
    render_page,
)
from shareomat.web.state import get_state


def _tariff_from_form(ctx: RequestContext) -> Tariff:
    valid_from = form_date(ctx, "valid_from")
    if valid_from is None:
        raise FormError("'Gültig ab' ist erforderlich.")
    return Tariff(
        local_rate_chf_kwh=form_decimal(ctx, "local_rate_chf_kwh", label="Lokaltarif CHF/kWh"),
        grid_rate_chf_kwh=form_decimal(ctx, "grid_rate_chf_kwh", label="Netztarif CHF/kWh"),
        feed_in_rate_chf_kwh=form_decimal(ctx, "feed_in_rate_chf_kwh", label="Einspeisetarif CHF/kWh"),
        name=form_required(ctx, "name", label="Bezeichnung"),
        valid_from=valid_from,
        valid_until=form_date(ctx, "valid_until"),
        active=form_bool(ctx, "active"),
    )


def handle_get(ctx: RequestContext) -> str:
    today = date.today()

    if not ctx.segments:
        tariffs = list_tariffs(ctx.db_path)
        current = get_tariff_for_date(ctx.db_path, today)
        return render_page(
            "tariffs/list.html", ctx, "tariffs",
            tariffs=tariffs, current=current, today=today.isoformat(),
        )

    if ctx.segments == ["new"]:
        return render_page("tariffs/form.html", ctx, "tariffs", tariff=None, tariff_id=None)

    if len(ctx.segments) == 2 and ctx.segments[1] == "edit":
        try:
            tariff_id = int(ctx.segments[0])
        except ValueError:
            tariff_id = -1
        tariff = get_tariff(ctx.db_path, tariff_id)
        if tariff is None:
            return render_page("placeholders/coming_soon.html", ctx, "tariffs",
                                title="Tarif nicht gefunden", description="")
        return render_page("tariffs/form.html", ctx, "tariffs", tariff=tariff, tariff_id=tariff_id)

    return render_page("placeholders/coming_soon.html", ctx, "tariffs",
                        title="Seite nicht gefunden", description="")


def handle_post(ctx: RequestContext) -> str | None:
    if ctx.segments == ["new"]:
        create_tariff(ctx.db_path, _tariff_from_form(ctx))
        get_state().set_flash("Tarif angelegt.", ok=True)
        return None

    if len(ctx.segments) == 2 and ctx.segments[1] == "edit":
        try:
            tariff_id = int(ctx.segments[0])
        except ValueError:
            raise FormError("Ungültige Tarif-ID.")
        try:
            update_tariff(ctx.db_path, tariff_id, _tariff_from_form(ctx))
        except ValueError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Tarif gespeichert.", ok=True)
        return None

    if len(ctx.segments) == 2 and ctx.segments[1] == "toggle":
        try:
            tariff_id = int(ctx.segments[0])
        except ValueError:
            raise FormError("Ungültige Tarif-ID.")
        tariff = get_tariff(ctx.db_path, tariff_id)
        if tariff is None:
            raise FormError("Tarif wurde nicht gefunden.")
        set_tariff_active(ctx.db_path, tariff_id, not tariff.active)
        state = "aktiviert" if not tariff.active else "deaktiviert"
        get_state().set_flash(f"Tarif {state}.", ok=True)
        return None

    raise FormError("Unbekannte Aktion.")
