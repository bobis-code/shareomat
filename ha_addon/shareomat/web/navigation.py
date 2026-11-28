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

from dataclasses import dataclass

# route name -> URL path (without the Ingress prefix, which url_for() adds).
ROUTE_PATHS: dict[str, str] = {
    "dashboard": "/",
    "login": "/login",
    "first_login": "/first-login",
    "logout": "/logout",
    "account": "/account",
    "setup": "/setup",
    "community": "/community",
    "participants": "/participants",
    "participants_new": "/participants/new",
    "meters": "/meters",
    "meters_new": "/meters/new",
    "tariffs": "/tariffs",
    "tariffs_new": "/tariffs/new",
    "contract": "/contract",
    "checklist": "/checklist",
    "email_templates": "/email-templates",
    "documents": "/documents",
    "meter_data": "/meter-data",
    "analysis": "/analysis",
    "analysis_sums": "/analysis/sums",
    "analysis_values": "/analysis/values",
    "billing": "/billing",
    "invoices": "/invoices",
    "automation": "/automation",
    "live_energy": "/live-energy",
    "live_energy_sources": "/live-energy/sources",
    "live_energy_diagnostics": "/live-energy/diagnostics",
    "settings": "/settings",
    "external_data": "/external-data",
    "apps": "/apps",
    "static": "/static",
    "banner": "/static/banner.png",
}

@dataclass(frozen=True)
class NavGroup:
    """One group of the main menu: a tab in the top bar (on phones: the bar at the bottom).

    `sections` are the pages shown in the side bar while a page of this group
    is open: (section title, ((route_name, label), ...)); "" = no heading.
    `icon` names an inline SVG in templates/components/nav_icons.html.
    """

    key: str
    label: str
    short_label: str          # under the icon in the bar at the bottom (phones)
    icon: str
    sections: tuple[tuple[str, tuple[tuple[str, str], ...]], ...]

    @property
    def routes(self) -> tuple[str, ...]:
        return tuple(route for _, items in self.sections for route, _ in items)

    @property
    def first_route(self) -> str:
        return self.routes[0]


# The main menu, grouped by task (19.11.2026, operator's decision): top tabs on a
# computer, a bar at the bottom on phones, the group's pages in the side bar.
NAV_GROUPS: tuple[NavGroup, ...] = (
    NavGroup("start", "Start", "Start", "home", (
        ("", (("dashboard", "Übersicht"),)),
    )),
    NavGroup("leg", "LEG-Verwaltung", "LEG", "users", (
        ("", (
            ("checklist", "Checkliste"),
            ("community", "Gemeinschaft"),
            ("participants", "Teilnehmer"),
            ("meters", "Messpunkte"),
            ("contract", "Vertrag"),
            ("tariffs", "Tarife"),
        )),
    )),
    NavGroup("energy", "Energie & Analyse", "Energie", "chart", (
        ("", (
            ("meter_data", "Messdaten"),
        )),
        # Official E66 data of the grid operator (20.11.2026, operator's decision): sums and values apart.
        ("ANALYSE", (
            ("analysis_sums", "Summen & Auswertung"),
            ("analysis_values", "Messwerte"),
        )),
        # Kept apart from the official data above (ADR-0007): live values never feed billing.
        ("LIVE-ENERGIE", (
            ("live_energy", "Übersicht"),
            ("live_energy_sources", "Messquellen"),
            ("live_energy_diagnostics", "Diagnose"),
        )),
    )),
    NavGroup("billing", "Abrechnung", "Abrechnung", "receipt", (
        ("", (
            ("billing", "Abrechnungen"),
            ("invoices", "Rechnungen"),
        )),
    )),
    NavGroup("documents", "Dokumente", "Dokumente", "folder", (
        ("", (
            ("documents", "Meldungen & Mitteilungen"),
            ("email_templates", "E-Mail-Vorlagen"),
        )),
    )),
    NavGroup("system", "System", "System", "gear", (
        ("", (
            ("settings", "Einstellungen"),
            ("automation", "Automatisierung"),
            ("external_data", "Externe Daten"),
            ("apps", "Apps & Integrationen"),
        )),
    )),
)

_GROUP_OF_ROUTE: dict[str, NavGroup] = {route: group for group in NAV_GROUPS for route in group.routes}
# Pages outside the menu are shown in the group they belong to.
_GROUP_OF_OTHER_ROUTE = {"account": "system", "setup": "start"}


def nav_group_for(route_name: str) -> NavGroup:
    """The menu group of a page (unknown pages: Start)."""
    group = _GROUP_OF_ROUTE.get(route_name)
    if group is not None:
        return group
    key = _GROUP_OF_OTHER_ROUTE.get(route_name, "start")
    return next(g for g in NAV_GROUPS if g.key == key)


# route names whose page is a placeholder in this phase — used to render the
# "in Vorbereitung" badge in the sidebar and to short-circuit their GET handler.
PLACEHOLDER_ROUTES: set[str] = set()


def url_for(ingress_path: str, route_name: str, *, suffix: str = "") -> str:
    """Build an absolute path for `route_name`, honoring the Home Assistant Ingress prefix.

    `suffix` appends extra path segments, e.g. url_for(p, "participants", suffix="/CH0001/edit").
    """
    path = ROUTE_PATHS[route_name]
    return f"{ingress_path}{path}{suffix}"
