# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/__init__.py

Purpose:
    One module per admin page. Each module exposes handle_get(ctx) -> str
    and, if the page accepts form submissions, handle_post(ctx) -> str|None
    (a redirect path, or None to redirect back to the page's own base
    route). Sub-routes (new/edit/toggle) are dispatched inside each page
    module via ctx.segments — web/server.py only routes by the first path
    segment.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""
