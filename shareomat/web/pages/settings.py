# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/settings.py

Purpose:
    "Einstellungen" admin page: basic processing rules (unknown-meter
    policy, archiving) plus a read-only view of the technical runtime
    configuration (managed via Docker/Home-Assistant add-on options, not
    editable here).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import replace

from shareomat.database.settings import get_operation_settings, save_operation_settings
from shareomat.leg_const import UNKNOWN_METER_POLICY_FAIL, UNKNOWN_METER_POLICY_SKIP
from shareomat.web.rendering import FormError, RequestContext, form_bool, render_page
from shareomat.web.state import get_state

UNKNOWN_METER_POLICIES = [
    (UNKNOWN_METER_POLICY_FAIL, "Lauf abbrechen (empfohlen)"),
    (UNKNOWN_METER_POLICY_SKIP, "Unbekannte Messwerte überspringen"),
]


def handle_get(ctx: RequestContext) -> str:
    settings = get_operation_settings(ctx.db_path)
    return render_page(
        "settings/edit.html", ctx, "settings",
        settings=settings, runtime=ctx.runtime, policies=UNKNOWN_METER_POLICIES,
    )


def handle_post(ctx: RequestContext) -> str | None:
    current = get_operation_settings(ctx.db_path)
    policy = (ctx.form.get("unknown_meter_policy") or "").strip()
    if policy not in {UNKNOWN_METER_POLICY_FAIL, UNKNOWN_METER_POLICY_SKIP}:
        raise FormError("Ungültige Auswahl bei 'Umgang mit unbekannten Messpunkten'.")

    updated = replace(
        current,
        unknown_meter_policy=policy,
        archive_processed=form_bool(ctx, "archive_processed"),
    )
    save_operation_settings(ctx.db_path, updated)
    get_state().set_flash("Einstellungen gespeichert.", ok=True)
    return None
