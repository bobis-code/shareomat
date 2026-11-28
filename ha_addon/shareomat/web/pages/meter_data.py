# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/meter_data.py

Purpose:
    "Messdaten" admin page: status of the grid operator's official data
    (SDAT: up to which day it arrived) with links to "Analyse". The file
    inbox, the manual upload and "Jetzt ausführen" went with the CSV path
    in 0.6.34 (ADR-0012); the official data arrives through the Swisseldex
    receiver ("Externe Daten").

    The sums per participant and day are in "Analyse → Summen &
    Auswertung" (web/pages/energy_analysis.py, 20.11.2026).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from shareomat.database.sdat_analysis import data_until
from shareomat.web.rendering import RequestContext, render_page


# ── Public interface ────────────────────────────────────────────────────────


def handle_get(ctx: RequestContext) -> str:
    """Render the status of the official data."""
    return render_page(
        "meter_data/list.html", ctx, "meter_data",
        official_until=data_until(ctx.db_path) if ctx.db_path else None,
    )
