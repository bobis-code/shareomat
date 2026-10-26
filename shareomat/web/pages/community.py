# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/community.py

Purpose:
    "Gemeinschaft" admin page: view and edit the single LEG/ZEV community.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from shareomat.core.pipeline.raw.sdat_ch import is_valid_eic, normalize_eic
from shareomat.database.community import get_community, save_community
from shareomat.models.community import Community
from shareomat.web.rendering import FormError, RequestContext, form_date, form_required, render_page


def handle_get(ctx: RequestContext) -> str:
    """Render the community edit form."""
    community = get_community(ctx.db_path) if ctx.db_path else None
    return render_page("community/edit.html", ctx, "community", community=community)


def _confirmed_start(ctx: RequestContext):
    value = form_date(ctx, "vnb_confirmed_start")
    if value is not None and value.day != 1:
        raise FormError("Der vom Netzbetreiber bestätigte Start muss ein Monatserster sein.")
    return value


def handle_post(ctx: RequestContext) -> str | None:
    """Validate and save the (single) community's master data."""
    community_id = form_required(ctx, "community_id", label="Gemeinschafts-ID")
    name = form_required(ctx, "name", label="Name")
    eic_code = normalize_eic(ctx.form.get("eic_code") or "")
    if eic_code and not is_valid_eic(eic_code):
        raise FormError(
            f"'{eic_code}' ist kein gültiger EIC (16 Zeichen, letzte Stelle ist "
            "eine Prüfziffer) — bitte mit dem Schreiben von Swissgrid vergleichen."
        )

    save_community(ctx.db_path, Community(
        community_id=community_id,
        name=name,
        address_line=(ctx.form.get("address_line") or "").strip(),
        postal_code=(ctx.form.get("postal_code") or "").strip(),
        city=(ctx.form.get("city") or "").strip(),
        active=True,
        eic_code=eic_code,
        vnb_registered_at=form_date(ctx, "vnb_registered_at"),
        vnb_confirmed_start=_confirmed_start(ctx),
    ))
    from shareomat.web.state import get_state
    get_state().set_flash("Gemeinschaft gespeichert.", ok=True)
    return None
