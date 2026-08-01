# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/automation.py

Purpose:
    "Automatisierung" admin page: cron schedule, automatic inbox
    scanning, and the (not yet acted upon) auto-invoicing switches.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Changing cron_schedule/auto_scan_enabled here only takes effect after
    the next Shareomat process restart — the scheduler/watcher threads
    are started once at startup (see SHAREOMAT_UMBAU_STRUKTUR.md §8).
"""

from __future__ import annotations

from dataclasses import replace

from shareomat.database.settings import get_operation_settings, save_operation_settings
from shareomat.web.rendering import FormError, RequestContext, form_bool, render_page
from shareomat.web.state import get_state


def handle_get(ctx: RequestContext) -> str:
    settings = get_operation_settings(ctx.db_path)
    return render_page("automation/edit.html", ctx, "automation", settings=settings)


def handle_post(ctx: RequestContext) -> str | None:
    current = get_operation_settings(ctx.db_path)

    scan_interval_raw = (ctx.form.get("scan_interval_seconds") or "").strip()
    try:
        scan_interval_seconds = int(scan_interval_raw) if scan_interval_raw else current.scan_interval_seconds
    except ValueError:
        raise FormError("Scan-Intervall muss eine ganze Zahl (Sekunden) sein.")
    if scan_interval_seconds < 5:
        raise FormError("Scan-Intervall muss mindestens 5 Sekunden betragen.")

    updated = replace(
        current,
        cron_schedule=(ctx.form.get("cron_schedule") or "").strip(),
        auto_scan_enabled=form_bool(ctx, "auto_scan_enabled"),
        scan_interval_seconds=scan_interval_seconds,
        auto_create_billing=form_bool(ctx, "auto_create_billing"),
        auto_create_invoices=form_bool(ctx, "auto_create_invoices"),
        auto_send_invoices=form_bool(ctx, "auto_send_invoices"),
    )
    save_operation_settings(ctx.db_path, updated)
    get_state().set_flash(
        "Automatisierung gespeichert. Änderungen an Zeitplan/Automatik-Scan werden nach einem "
        "Neustart von Shareomat aktiv.",
        ok=True,
    )
    return None
