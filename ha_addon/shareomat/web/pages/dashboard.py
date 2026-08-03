# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/dashboard.py

Purpose:
    "Übersicht" — the admin UI landing page: status tiles for the whole
    system plus a concrete "offene Aufgaben" (open tasks) list, per
    SHAREOMAT_UMBAU_STRUKTUR.md §11.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import logging

from shareomat.database.config_builder import get_setup_status
from shareomat.database.meters import list_meters
from shareomat.database.participants import list_participants
from shareomat.database.settings import get_operation_settings
from shareomat.database.tariffs import get_tariff_for_date
from shareomat.web.rendering import RequestContext, render_page
from shareomat.web.state import get_state

_KNOWN_STATUS = {"ok", "error", "starting", "offline"}

logger = logging.getLogger(__name__)


def _open_tasks(ctx: RequestContext, snapshot: dict) -> list[str]:
    tasks: list[str] = []

    setup_complete = False
    if ctx.db_path is not None:
        setup_status = get_setup_status(ctx.db_path)
        setup_complete = setup_status.is_complete
        if not setup_status.has_community:
            tasks.append("Gemeinschaft einrichten.")
        if not setup_status.has_participants:
            tasks.append("Mindestens einen aktiven Teilnehmer anlegen.")
        if not setup_status.has_meters:
            tasks.append("Mindestens einen aktiven Messpunkt anlegen.")
        if not setup_status.has_tariff:
            tasks.append("Einen für heute gültigen Tarif anlegen.")

    if snapshot.get("last_error"):
        tasks.append(f"Letzter Lauf fehlgeschlagen: {snapshot['last_error']}")

    if setup_complete and snapshot.get("inbox_count", 0) > 0:
        tasks.append(f"{snapshot['inbox_count']} Datei(en) im Eingang — Verarbeitung starten.")

    return tasks


def handle_get(ctx: RequestContext) -> str:
    """Render the system-status overview with counts, recent runs, and pending tasks."""
    state = get_state()
    snapshot = state.get()
    status = snapshot["status"]
    if status not in _KNOWN_STATUS:
        logger.warning("Unknown web status '%s'; using 'starting' CSS class", status)

    participants_active = participants_total = 0
    meters_active = meters_total = 0
    tariff_ok = False
    automation_active = False
    setup_incomplete = False

    if ctx.db_path is not None:
        setup_incomplete = not get_setup_status(ctx.db_path).is_complete
        participants = list_participants(ctx.db_path)
        participants_total = len(participants)
        participants_active = sum(1 for p in participants if p.active)

        meters = list_meters(ctx.db_path)
        meters_total = len(meters)
        meters_active = sum(1 for m in meters if m.active)

        from datetime import date
        tariff_ok = get_tariff_for_date(ctx.db_path, date.today()) is not None

        settings = get_operation_settings(ctx.db_path)
        automation_active = settings.auto_scan_enabled or bool(settings.cron_schedule)

    return render_page(
        "dashboard.html", ctx, "dashboard",
        status=status,
        last_run=snapshot["last_run"],
        inbox_count=snapshot["inbox_count"],
        report_count=snapshot["report_count"],
        participants_active=participants_active,
        participants_total=participants_total,
        meters_active=meters_active,
        meters_total=meters_total,
        tariff_ok=tariff_ok,
        automation_active=automation_active,
        setup_incomplete=setup_incomplete,
        open_tasks=_open_tasks(ctx, snapshot),
    )
