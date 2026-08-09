# -*- coding: utf-8 -*-
"""
File: shareomat/web/navigation.py

Purpose:
    Sidebar navigation structure and URL generation for the admin web
    interface. One place defines every route path so links, form
    actions, and redirects can never drift apart.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    url_for() prepends the Home-Assistant Ingress path prefix
    (X-Ingress-Path header) when running behind Ingress, and is a no-op
    prefix when running standalone — see web/server.py.
"""

from __future__ import annotations

# route name -> URL path (without the Ingress prefix, which url_for() adds).
ROUTE_PATHS: dict[str, str] = {
    "dashboard": "/",
    "setup": "/setup",
    "community": "/community",
    "participants": "/participants",
    "participants_new": "/participants/new",
    "meters": "/meters",
    "meters_new": "/meters/new",
    "tariffs": "/tariffs",
    "tariffs_new": "/tariffs/new",
    "contract": "/contract",
    "meter_data": "/meter-data",
    "analysis": "/analysis",
    "billing": "/billing",
    "invoices": "/invoices",
    "automation": "/automation",
    "reports": "/reports",
    "reports_download": "/reports/download",
    "settings": "/settings",
    "external_data": "/external-data",
    "run": "/actions/run",
    "static": "/static",
    "banner": "/static/banner.png",
}

# (section title, [(route_name, label), ...]) — section title "" renders without a heading.
NAV_SECTIONS: list[tuple[str, list[tuple[str, str]]]] = [
    ("", [("dashboard", "Übersicht")]),
    ("VERWALTUNG", [
        ("community", "Gemeinschaft"),
        ("participants", "Teilnehmer"),
        ("meters", "Messpunkte"),
        ("tariffs", "Tarife"),
        ("contract", "Vertrag"),
    ]),
    ("BETRIEB", [
        ("meter_data", "Messdaten"),
        ("analysis", "Analyse"),
        ("billing", "Abrechnungen"),
        ("invoices", "Rechnungen"),
        ("automation", "Automatisierung"),
        ("reports", "Berichte"),
    ]),
    ("SYSTEM", [
        ("settings", "Einstellungen"),
        ("external_data", "Externe Daten"),
    ]),
]

# route names whose page is a placeholder in this phase — used to render the
# "in Vorbereitung" badge in the sidebar and to short-circuit their GET handler.
PLACEHOLDER_ROUTES = {"invoices"}


def url_for(ingress_path: str, route_name: str, *, suffix: str = "") -> str:
    """Build an absolute path for `route_name`, honoring the Home Assistant Ingress prefix.

    `suffix` appends extra path segments, e.g. url_for(p, "participants", suffix="/CH0001/edit").
    """
    path = ROUTE_PATHS[route_name]
    return f"{ingress_path}{path}{suffix}"
