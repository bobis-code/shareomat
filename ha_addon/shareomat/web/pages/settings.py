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
from shareomat.leg_const import (
    METER_DATA_SOURCE_EMAIL_CSV,
    METER_DATA_SOURCE_SDAT_LEG,
    UNKNOWN_METER_POLICY_FAIL,
    UNKNOWN_METER_POLICY_SKIP,
)
from shareomat.web.rendering import FormError, RequestContext, form_bool, render_page
from shareomat.web.state import get_state

UNKNOWN_METER_POLICIES = [
    (UNKNOWN_METER_POLICY_FAIL, "Lauf abbrechen (empfohlen)"),
    (UNKNOWN_METER_POLICY_SKIP, "Unbekannte Messwerte überspringen"),
]

METER_DATA_SOURCES = [
    (METER_DATA_SOURCE_EMAIL_CSV, "E-Mail / CSV (aktuell)"),
    (METER_DATA_SOURCE_SDAT_LEG, "SDAT (EBL/Swisseldex) — in Vorbereitung"),
]


def handle_get(ctx: RequestContext) -> str:
    """Render processing settings plus a read-only technical-config summary."""
    settings = get_operation_settings(ctx.db_path)
    return render_page(
        "settings/edit.html", ctx, "settings",
        settings=settings, runtime=ctx.runtime, policies=UNKNOWN_METER_POLICIES,
        meter_data_sources=METER_DATA_SOURCES,
    )


def handle_post(ctx: RequestContext) -> str | None:
    """Validate and save processing settings."""
    current = get_operation_settings(ctx.db_path)
    policy = (ctx.form.get("unknown_meter_policy") or "").strip()
    if policy not in {UNKNOWN_METER_POLICY_FAIL, UNKNOWN_METER_POLICY_SKIP}:
        raise FormError("Ungültige Auswahl bei 'Umgang mit unbekannten Messpunkten'.")

    meter_data_source = (ctx.form.get("meter_data_source") or "").strip()
    if meter_data_source not in {METER_DATA_SOURCE_EMAIL_CSV, METER_DATA_SOURCE_SDAT_LEG}:
        raise FormError("Ungültige Auswahl bei 'Messdaten-Quelle'.")

    updated = replace(
        current,
        unknown_meter_policy=policy,
        archive_processed=form_bool(ctx, "archive_processed"),
        meter_data_source=meter_data_source,
    )
    save_operation_settings(ctx.db_path, updated)
    get_state().set_flash("Einstellungen gespeichert.", ok=True)
    return None
