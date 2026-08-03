# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/meter_data.py

Purpose:
    "Messdaten" admin page: inbox overview, manual file upload, and the
    manual "Jetzt ausführen" trigger. The actual upload (multipart) and
    run-trigger POSTs are handled directly in web/server.py (streaming
    upload needs special handling BaseHTTPRequestHandler doesn't give a
    generic urlencoded-form page) — this module only renders the GET view.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from shareomat.web.rendering import RequestContext, render_page


def handle_get(ctx: RequestContext) -> str:
    """Render the inbox file list and upload/run-now forms."""
    runtime = ctx.runtime
    inbox_files: list[str] = []
    if runtime is not None and runtime.paths.inbox.exists():
        inbox_files = sorted(f.name for f in runtime.paths.inbox.iterdir() if f.is_file())

    return render_page(
        "meter_data/list.html", ctx, "meter_data",
        inbox_files=inbox_files,
    )
