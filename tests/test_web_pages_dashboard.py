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
    state.update(status="paused", last_run="-", inbox_count=0, report_count=0, last_error="")

    with caplog.at_level(logging.WARNING):
        html = dashboard.handle_get(_ctx())

    assert "paused" in html
    assert "Unknown web status 'paused'" in caplog.text


def test_dashboard_shows_open_task_for_last_error() -> None:
    state = get_state()
    state.update(
        status="error", last_run="2026-01-01 12:00 UTC", inbox_count=0, report_count=0,
        last_error="Settlement failed: missing column",
    )

    html = dashboard.handle_get(_ctx())
    assert "Settlement failed: missing column" in html
    assert "error" in html


def test_dashboard_shows_startup_warnings() -> None:
    state = get_state()
    state.update(status="ok", last_run="-", inbox_count=0, report_count=0, last_error="")
    state.add_warning("MQTT is enabled but Shareomat could not connect to the broker.")

    html = dashboard.handle_get(_ctx())
    assert "Hinweise" in html
    assert "MQTT is enabled" in html


def test_dashboard_without_db_shows_zero_counts() -> None:
    """No database registered yet (never happens in practice, but must not crash)."""
    state = get_state()
    state.update(status="starting", last_run="-", inbox_count=0, report_count=0, last_error="")

    html = dashboard.handle_get(_ctx())
    assert "0 / 0 aktiv" in html
