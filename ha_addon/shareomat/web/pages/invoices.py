# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/invoices.py

Purpose:
    "Rechnungen" placeholder page. Real invoices (with status, PDF,
    sending, payment tracking) do not exist yet — see
    SHAREOMAT_UMBAU_STRUKTUR.md §15.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from shareomat.web.rendering import RequestContext, render_page


def handle_get(ctx: RequestContext) -> str:
    return render_page(
        "placeholders/coming_soon.html", ctx, "invoices",
        title="Rechnungen",
        description="Rechnungsstellung (PDF, Versand, Zahlungsstatus, Mahnungen) ist in Vorbereitung.",
    )
