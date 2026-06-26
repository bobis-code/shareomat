# -*- coding: utf-8 -*-
"""Tests for Ingress dashboard rendering."""

from __future__ import annotations

import logging

from self_leg.ha.web_render import render_dashboard
from self_leg.ha.web_state import get_state


def test_render_dashboard_logs_unknown_status(caplog) -> None:
    state = get_state()
    state.update(
        status="paused",
        last_run="-",
        inbox_count=0,
        report_count=0,
        last_error="",
    )
    state.register_meters([])
    state.register_reports(None)

    with caplog.at_level(logging.WARNING):
        html = render_dashboard("", "")

    assert 'status-starting">paused<' in html
    assert "Unknown ingress status 'paused'" in caplog.text


def test_render_dashboard_shows_last_error() -> None:
    state = get_state()
    state.update(
        status="error",
        last_run="2026-01-01 12:00 UTC",
        inbox_count=0,
        report_count=0,
        last_error="Settlement failed: missing column",
    )
    state.register_meters([])
    state.register_reports(None)

    html = render_dashboard("", "")
    assert "Settlement failed: missing column" in html
    assert 'class="error-box"' in html
    assert 'status-error">error<' in html


def test_render_dashboard_shows_startup_warnings() -> None:
    state = get_state()
    state.update(
        status="ok",
        last_run="-",
        inbox_count=0,
        report_count=0,
        last_error="",
    )
    state.register_meters([])
    state.register_reports(None)
    state.add_warning("MQTT is enabled but SELF LEG could not connect to the broker.")

    html = render_dashboard("", "")
    assert "Startup warning" in html
    assert "MQTT is enabled" in html
