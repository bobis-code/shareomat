# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/automation.py

Purpose:
    "Automatisierung" admin page. Shows the three automation switches
    (create billing, create invoices, send invoices) greyed out as "noch
    nicht verfügbar", and what already runs by itself.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The switches were stored but never acted upon (B17). Until the
    automation concept per LEG is built (E8, P14 of ADR-0012) they are
    shown disabled and nothing can be saved. The cron schedule and the
    automatic inbox scan went with the CSV path in 0.6.34 (ADR-0012).
"""

from __future__ import annotations

from shareomat.web.rendering import FormError, RequestContext, render_page


def handle_get(ctx: RequestContext) -> str:
    """Render the automation page (read-only until the automation concept is built)."""
    return render_page("automation/edit.html", ctx, "automation")


def handle_post(ctx: RequestContext) -> str | None:
    """Nothing to save yet: the switches are not available (E8)."""
    raise FormError("Die Automatisierung ist noch nicht verfügbar. Es wurde nichts gespeichert.")
