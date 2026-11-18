# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/meter_data.py

Purpose:
    "Messdaten" admin page: month overview of the grid operator's official
    data (SDAT, per participant and per day, read-only), inbox overview,
    manual file upload, and the manual "Jetzt ausführen" trigger. The
    actual upload (multipart) and run-trigger POSTs are handled directly in
    web/server.py (streaming upload needs special handling
    BaseHTTPRequestHandler doesn't give a generic urlencoded-form page) —
    this module only renders the GET view.

    GET /meter-data?monat=YYYY-MM   official data of that month (default: current month)

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import re
from datetime import date

from shareomat.database.sdat_overview import month_overview
from shareomat.database.settings import get_operation_settings
from shareomat.leg_const import METER_DATA_SOURCE_SDAT_LEG
from shareomat.web.rendering import RequestContext, render_page

_MONTH_PARAM = re.compile(r"^(\d{4})-(\d{2})$")
MONTH_NAMES = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
               "November", "Dezember")


# ── Private helpers ─────────────────────────────────────────────────────────


def _requested_month(ctx: RequestContext) -> date:
    """?monat=YYYY-MM within 2000..2100, otherwise the current month."""
    match = _MONTH_PARAM.match(ctx.query.get("monat", ""))
    if match:
        year, month = int(match.group(1)), int(match.group(2))
        if 2000 <= year <= 2100 and 1 <= month <= 12:
            return date(year, month, 1)
    return date.today().replace(day=1)


def _shift(month: date, months: int) -> date:
    index = month.year * 12 + month.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


# ── Public interface ────────────────────────────────────────────────────────


def handle_get(ctx: RequestContext) -> str:
    """Render the official month overview, the inbox file list and upload/run-now forms."""
    runtime = ctx.runtime
    inbox_files: list[str] = []
    if runtime is not None and runtime.paths.inbox.exists():
        inbox_files = sorted(f.name for f in runtime.paths.inbox.iterdir() if f.is_file())

    month = _requested_month(ctx)
    overview = month_overview(ctx.db_path, month) if ctx.db_path else None
    billing_uses_sdat = bool(ctx.db_path) and (
        get_operation_settings(ctx.db_path).meter_data_source == METER_DATA_SOURCE_SDAT_LEG)
    return render_page(
        "meter_data/list.html", ctx, "meter_data",
        inbox_files=inbox_files,
        overview=overview,
        month_label=f"{MONTH_NAMES[month.month - 1]} {month.year}",
        previous_month=_shift(month, -1).strftime("%Y-%m"),
        next_month=_shift(month, 1).strftime("%Y-%m"),
        billing_uses_sdat=billing_uses_sdat,
    )
