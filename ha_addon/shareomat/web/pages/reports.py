# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/reports.py

Purpose:
    "Berichte" admin page: browse and download past settlement reports.
    Delegates the actual HTML table/KPI rendering to shareomat.web.reports
    (ported from the old single-page dashboard), which stays functional
    unchanged per SHAREOMAT_UMBAU_STRUKTUR.md §15.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from shareomat.web.rendering import RequestContext, render_page
from shareomat.web.reports import list_billing_reports, render_report_section


def handle_get(ctx: RequestContext) -> str:
    """Render the report list, plus the selected report's content if any."""
    selected = ctx.query.get("report", "")
    report_list = list_billing_reports()

    content_html = ""
    if selected:
        known = {stem for stem, _ in report_list}
        if selected in known:
            content_html = render_report_section(selected, ctx.ingress_path)
        else:
            content_html = '<div class="error-box">Bericht nicht gefunden.</div>'

    return render_page(
        "reports/list.html", ctx, "reports",
        report_list=report_list, selected=selected, content_html=content_html,
    )
