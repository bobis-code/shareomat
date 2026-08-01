# -*- coding: utf-8 -*-
"""Tests for admin web interface report discovery helpers (shareomat.web.reports)."""

from __future__ import annotations

from pathlib import Path

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.web.reports import list_billing_reports, render_report_section, report_path
from shareomat.web.state import get_state


def _register_reports(tmp_path: Path) -> None:
    get_state().register_runtime(RuntimeConfig(paths=PathConfig(
        inbox=tmp_path, archive=tmp_path, reports=tmp_path, state=tmp_path,
    )))


def test_list_billing_reports_ignores_empty_suffix(tmp_path) -> None:
    _register_reports(tmp_path)
    (tmp_path / "billing_20260401_000000.json").write_text("[]", encoding="utf-8")
    (tmp_path / "billing_.json").write_text("[]", encoding="utf-8")
    (tmp_path / "community_summary_20260401_000000.json").write_text("{}", encoding="utf-8")

    assert list_billing_reports() == [("billing_20260401_000000", "2026-04-01  00:00")]


def test_report_path_constrains_lookup_to_reports_directory(tmp_path) -> None:
    _register_reports(tmp_path)
    report = tmp_path / "billing_20260401_000000.json"
    report.write_text("[]", encoding="utf-8")

    assert report_path("billing_20260401_000000.json") == report
    assert report_path("../billing_20260401_000000.json") == report
    assert report_path("../missing.json") is None


def test_render_report_section_with_broken_json(tmp_path) -> None:
    _register_reports(tmp_path)
    (tmp_path / "billing_20260401_000000.json").write_text("NOT VALID JSON", encoding="utf-8")

    result = render_report_section("billing_20260401_000000", "")
    assert "error-box" in result
    assert "Could not read report file" in result
