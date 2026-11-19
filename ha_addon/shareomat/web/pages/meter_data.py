# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/meter_data.py

Purpose:
    "Messdaten" admin page: status of the grid operator's official data
    (SDAT: up to which day it arrived, links to "Analyse"), inbox overview,
    manual file upload, and the manual "Jetzt ausführen" trigger. The
    actual upload (multipart) and run-trigger POSTs are handled directly in
    web/server.py (streaming upload needs special handling
    BaseHTTPRequestHandler doesn't give a generic urlencoded-form page) —
    this module only renders the GET view.

    The sums per participant and day moved to "Analyse → Summen &
    Auswertung" (web/pages/energy_analysis.py, 20.11.2026).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from shareomat.database.sdat_analysis import data_until
from shareomat.database.settings import get_operation_settings
from shareomat.leg_const import METER_DATA_SOURCE_SDAT_LEG
from shareomat.web.rendering import RequestContext, render_page


# ── Public interface ────────────────────────────────────────────────────────


def handle_get(ctx: RequestContext) -> str:
    """Render the official data status, the inbox file list and upload/run-now forms."""
    runtime = ctx.runtime
    inbox_files: list[str] = []
    if runtime is not None and runtime.paths.inbox.exists():
        inbox_files = sorted(f.name for f in runtime.paths.inbox.iterdir() if f.is_file())

    billing_uses_sdat = bool(ctx.db_path) and (
        get_operation_settings(ctx.db_path).meter_data_source == METER_DATA_SOURCE_SDAT_LEG)
    return render_page(
        "meter_data/list.html", ctx, "meter_data",
        inbox_files=inbox_files,
        official_until=data_until(ctx.db_path) if ctx.db_path else None,
        billing_uses_sdat=billing_uses_sdat,
    )
