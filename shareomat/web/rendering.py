# -*- coding: utf-8 -*-
"""
File: shareomat/web/rendering.py

Purpose:
    Jinja2 environment, the per-request context object, and small
    exceptions page handlers use to signal a redirect or a validation
    error. Kept separate from web/server.py only to avoid a circular
    import (server.py dispatches to web/pages/*, and every page needs
    these same few things) — this is not a general request/response
    framework, just the shared plumbing every page template needs.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

import jinja2

from shareomat.config import RuntimeConfig
from shareomat.web.navigation import NAV_SECTIONS, PLACEHOLDER_ROUTES
from shareomat.web.navigation import url_for as _url_for
from shareomat.web.state import get_state

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=True,
)


@dataclass
class RequestContext:
    """Everything a page handler needs about the current request."""

    method: str
    ingress_path: str
    segments: list[str] = field(default_factory=list)
    query: dict[str, str] = field(default_factory=dict)
    form: dict[str, str] = field(default_factory=dict)

    @property
    def db_path(self) -> Path | None:
        return get_state().db_path

    @property
    def runtime(self) -> RuntimeConfig | None:
        return get_state().runtime


class FormError(Exception):
    """Raise from a POST handler to redirect back with a flash error message."""


class Redirect(Exception):
    """Raise from a POST handler to redirect somewhere other than its own page."""

    def __init__(self, location: str) -> None:
        super().__init__(location)
        self.location = location


def check_csrf(ctx: RequestContext) -> None:
    """Raise FormError if the submitted CSRF token does not match the process token."""
    if ctx.form.get("csrf_token") != get_state().csrf_token:
        raise FormError(
            "Ungültige Anfrage (Sicherheits-Token fehlt oder ist abgelaufen). "
            "Bitte die Seite neu laden und erneut versuchen."
        )


def form_date(ctx: RequestContext, field_name: str) -> date | None:
    """Parse an optional <input type=date> field; '' or missing -> None."""
    raw = (ctx.form.get(field_name) or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise FormError(f"Ungültiges Datum bei '{field_name}': {raw}")


def form_bool(ctx: RequestContext, field_name: str) -> bool:
    """A checkbox is present in form data only when checked."""
    return field_name in ctx.form


def form_decimal(ctx: RequestContext, field_name: str, *, label: str) -> Decimal:
    """Parse a required decimal money/rate field, raising a friendly FormError on bad input."""
    raw = (ctx.form.get(field_name) or "").strip().replace(",", ".")
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise FormError(f"Ungültiger Wert bei '{label}': '{raw}'")


def form_required(ctx: RequestContext, field_name: str, *, label: str) -> str:
    """Return a required text field, stripped, raising a friendly FormError if empty."""
    value = (ctx.form.get(field_name) or "").strip()
    if not value:
        raise FormError(f"'{label}' darf nicht leer sein.")
    return value


def render_page(template_name: str, ctx: RequestContext, active_route: str, **extra) -> str:
    """Render a Jinja2 template with the shared admin-UI context (nav, flash, CSRF, ...)."""
    state = get_state()
    flash_message, flash_ok = state.pop_flash()
    snapshot = state.get()

    community_name = ""
    if ctx.db_path is not None:
        try:
            from shareomat.database.community import get_community
            community = get_community(ctx.db_path)
            if community:
                community_name = community.name
        except Exception:
            community_name = ""

    def _url_for_bound(route_name: str, *, suffix: str = "") -> str:
        return _url_for(ctx.ingress_path, route_name, suffix=suffix)

    template = _env.get_template(template_name)
    context = dict(
        url_for=_url_for_bound,
        nav_sections=NAV_SECTIONS,
        placeholder_routes=PLACEHOLDER_ROUTES,
        active_route=active_route,
        community_name=community_name,
        flash_message=flash_message,
        flash_ok=flash_ok,
        warnings=snapshot.get("warnings", []),
        csrf_token=state.csrf_token,
        ctx=ctx,
    )
    context.update(extra)
    return template.render(**context)
