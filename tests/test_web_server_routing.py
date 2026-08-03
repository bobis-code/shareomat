# -*- coding: utf-8 -*-
"""
Regression tests for shareomat.web.server routing.

Guards against the exact bug found in production: a hyphenated URL path
segment (e.g. "external-data") used directly as a route name for
url_for(), which only knows underscored route names ("external_data") —
raised an uncaught KeyError inside do_POST/do_GET, which a client sees
as a dropped connection ("502 Bad Gateway" behind a reverse proxy like
HA Ingress). No existing page-level test caught this because those call
page.handle_get()/handle_post() directly, bypassing server.py's routing
entirely.
"""

from __future__ import annotations

import pytest

from shareomat.web.navigation import ROUTE_PATHS, url_for
from shareomat.web.server import _PAGE_MODULES


def test_every_page_module_key_is_a_valid_route_name():
    """Every _PAGE_MODULES key must work as a url_for() route name.

    This is exactly what server.py's do_GET/do_POST fallback does with
    the first URL path segment — if a key here ever drifts from
    underscored route-name form (e.g. someone adds "foo-bar" instead of
    "foo_bar"), this test fails instead of a live 502.
    """
    for route in _PAGE_MODULES:
        if route == "":
            continue  # the dashboard's route name really is "" for GET, but "dashboard" for url_for
        url_for("", route)  # must not raise KeyError


def test_hyphenated_url_segments_normalize_to_known_routes():
    """URLs use hyphens (nicer to read); route names/dict keys use underscores."""
    for hyphenated_path in ("meter-data", "external-data"):
        normalized = hyphenated_path.replace("-", "_")
        assert normalized in _PAGE_MODULES, f"{hyphenated_path} -> {normalized} not registered"
        assert normalized in ROUTE_PATHS, f"{normalized} missing from ROUTE_PATHS"


def test_dashboard_route_path_is_root():
    assert ROUTE_PATHS["dashboard"] == "/"


def test_route_path_round_trips_back_to_its_own_page_module_key():
    """A route's own URL, parsed the same way server.py parses incoming requests, must
    dispatch back to that same _PAGE_MODULES entry.

    Catches a different flavor of the same bug class: ROUTE_PATHS/url_for() using a
    German URL word (e.g. "/analyse") while _PAGE_MODULES/the route name use the
    English word ("analysis") — every other check here passed (both dicts had
    matching keys), but a real request to that URL would 404 because the first path
    segment ("analyse") never matches the dict key ("analysis").
    """
    for route, page in _PAGE_MODULES.items():
        if route == "":
            continue  # dashboard is served at "/", which has no path segment to parse
        path = ROUTE_PATHS[route]
        segment = path.strip("/").split("/")[0]
        normalized = segment.replace("-", "_")
        assert normalized == route, (
            f"ROUTE_PATHS['{route}'] = '{path}' does not route back to '{route}' "
            f"(first segment normalizes to '{normalized}')"
        )
