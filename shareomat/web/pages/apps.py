# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/apps.py

Purpose:
    "Apps & Integrationen" admin page: shows companion apps next to
    Shareomat (currently Emsomat) — installed or not, version, live
    online state — and links to install, set up, and open them in
    Home Assistant.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Read-only, no POST. Shareomat neither installs nor configures
    Emsomat: installing/updating stays with HACS, configuring devices
    stays in Emsomat itself — this page only shows and links.
"""

from __future__ import annotations

from shareomat.ha.emsomat_install import read_emsomat_install
from shareomat.web.rendering import RequestContext, render_page
from shareomat.web.state import get_state

# my.home-assistant.io redirects open the page in the user's own Home
# Assistant instance (standard, documented HA mechanism).
EMSOMAT_LINKS = {
    "install": "https://my.home-assistant.io/redirect/hacs_repository/?owner=bobis-code&repository=Emsomat&category=integration",
    "setup": "https://my.home-assistant.io/redirect/config_flow_start/?domain=emsomat",
    "open": "https://my.home-assistant.io/redirect/integration/?domain=emsomat",
}


def handle_get(ctx: RequestContext) -> str:
    """Render the apps overview with the Emsomat card."""
    return render_page(
        "apps/index.html", ctx, "apps",
        emsomat=read_emsomat_install(),
        emsomat_online=get_state().emsomat_online,
        emsomat_links=EMSOMAT_LINKS,
    )
