# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/analysis.py

Purpose:
    Routes "Analyse" to its pages in energy_analysis.py: "Summen &
    Auswertung" (/analysis and /analysis/sums) and "Messwerte"
    (/analysis/values), both on the grid operator's official E66 data.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    "Analyse → Übersicht" showed the results of the automatic CSV
    settlement runs (settlement_history); it went with the CSV path in
    0.6.34 (ADR-0012).
"""

from __future__ import annotations

from shareomat.web.pages import energy_analysis
from shareomat.web.rendering import RequestContext, render_page


def handle_get(ctx: RequestContext) -> str:
    """Summen & Auswertung for /analysis and /analysis/sums, Messwerte for /analysis/values."""
    if ctx.segments in ([], ["sums"]):
        return energy_analysis.sums_page(ctx)
    if ctx.segments == ["values"]:
        return energy_analysis.values_page(ctx)
    return render_page("placeholders/coming_soon.html", ctx, "analysis", title="Seite nicht gefunden",
                       description="")
