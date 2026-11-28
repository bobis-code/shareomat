# -*- coding: utf-8 -*-
"""Tests for the admin web interface's dashboard page (shareomat.web.pages.dashboard)."""

from __future__ import annotations

import logging

import shareomat.web.pages.dashboard as dashboard
from shareomat.web.rendering import RequestContext
from shareomat.web.state import get_state


def _ctx() -> RequestContext:
    return RequestContext(method="GET", ingress_path="")


def test_dashboard_logs_unknown_status(caplog) -> None:
    state = get_state()
    state.update(status="paused")

    with caplog.at_level(logging.WARNING):
        html = dashboard.handle_get(_ctx())

    assert "paused" in html
    assert "Unknown web status 'paused'" in caplog.text


def test_dashboard_has_no_tiles_of_the_removed_csv_run() -> None:
    """Inbox files, reports, automation and "last run" belonged to the CSV run (removed in 0.6.34)."""
    get_state().update(status="ok")

    html = dashboard.handle_get(_ctx())
    for gone in ("Messdaten im Eingang", "Berichte", "Letzter Lauf", "Lauf starten", "Automatik"):
        assert gone not in html, gone
    assert "Abrechnungen" in html


def test_dashboard_shows_startup_warnings() -> None:
    state = get_state()
    state.update(status="ok")
    state.add_warning("MQTT is enabled but Shareomat could not connect to the broker.")

    html = dashboard.handle_get(_ctx())
    assert "Hinweise" in html
    assert "MQTT is enabled" in html


def test_dashboard_without_db_shows_zero_counts() -> None:
    """No database registered yet (never happens in practice, but must not crash)."""
    state = get_state()
    state.update(status="starting")

    html = dashboard.handle_get(_ctx())
    assert "0 / 0 aktiv" in html
