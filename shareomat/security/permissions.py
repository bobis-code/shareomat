# -*- coding: utf-8 -*-
"""
File: shareomat/security/permissions.py

Purpose:
    Role model and the central route -> permission map. Prepared for
    several users and roles; for now there is one role, "admin", which
    holds every permission.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The web server asks `allowed(principal, method, route, segments)` for
    EVERY request after login (pages, downloads, prints, uploads,
    actions). Adding a role later means: add it to ROLES with its
    permission set — no change to the login or the server.
"""

from __future__ import annotations

from shareomat.security.context import Principal

PERMISSIONS = {
    "view": "Ansehen",
    "billing.approve": "Abrechnungen berechnen und freigeben",
    "invoice.manage": "Belege erstellen, versenden, stornieren, mahnen",
    "payment.record": "Zahlungseingänge und Rückerstattungen erfassen",
    "payout.record": "Auszahlungen erfassen",
    "payment_profile.change": "Bankkonto ändern",
    "meterdata.import": "Messdaten hochladen und verarbeiten",
    "masterdata.edit": "Gemeinschaft, Teilnehmer, Messpunkte, Tarife bearbeiten",
    "contract.manage": "Vertrag bearbeiten und veröffentlichen",
    "system.configure": "System und Automatisierung einstellen",
    "account.self": "Eigenes Konto (Passwort, Geräte)",
}

ROLES = {
    "admin": ("Administrator", frozenset(PERMISSIONS)),
}

_POST_PERMISSIONS = {
    "billing": "billing.approve",
    "meter_data": "meterdata.import",
    "actions": "meterdata.import",
    "community": "masterdata.edit",
    "participants": "masterdata.edit",
    "meters": "masterdata.edit",
    "tariffs": "masterdata.edit",
    "contract": "contract.manage",
    "settings": "system.configure",
    "automation": "system.configure",
    "external_data": "system.configure",
    "setup": "system.configure",
    "account": "account.self",
}


def required_permission(method: str, route: str, segments: list[str]) -> str:
    """The permission an action needs. Unknown POST routes need the strongest one."""
    if method != "POST":
        return "account.self" if route == "account" else "view"
    if route == "invoices":
        if segments[:1] == ["settings"]:
            return "payment_profile.change"
        action = segments[1] if len(segments) == 2 else ""
        if action in ("payment", "refund"):
            return "payment.record"
        if action == "payout":
            return "payout.record"
        return "invoice.manage"
    return _POST_PERMISSIONS.get(route, "system.configure")


def has_permission(principal: Principal | None, permission: str) -> bool:
    if principal is None:
        return False
    role = ROLES.get(principal.role)
    return role is not None and permission in role[1]
