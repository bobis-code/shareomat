# -*- coding: utf-8 -*-
"""Tests for Ingress report discovery helpers."""

from __future__ import annotations

from self_leg.ha.web_reports import _render_report_section, list_billing_reports, report_path
from self_leg.ha.web_state import get_state


def test_list_billing_reports_ignores_empty_suffix(tmp_path) -> None:
    state = get_state()
    state.register_reports(tmp_path)
    (tmp_path / "billing_20260401_000000.json").write_text("[]", encoding="utf-8")
    (tmp_path / "billing_.json").write_text("[]", encoding="utf-8")
    (tmp_path / "community_summary_20260401_000000.json").write_text("{}", encoding="utf-8")

    assert list_billing_reports() == [("billing_20260401_000000", "2026-04-01  00:00")]


def test_report_path_constrains_lookup_to_reports_directory(tmp_path) -> None:
    state = get_state()
    state.register_reports(tmp_path)
    report = tmp_path / "billing_20260401_000000.json"
    report.write_text("[]", encoding="utf-8")

    assert report_path("billing_20260401_000000.json") == report
    assert report_path("../billing_20260401_000000.json") == report
    assert report_path("../missing.json") is None


def test_render_report_section_with_broken_json(tmp_path) -> None:
    state = get_state()
    state.register_reports(tmp_path)
    (tmp_path / "billing_20260401_000000.json").write_text("NOT VALID JSON", encoding="utf-8")

    result = _render_report_section("billing_20260401_000000", "")
    assert "error-box" in result
    assert "Could not read report file" in result
