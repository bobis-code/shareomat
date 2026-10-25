# -*- coding: utf-8 -*-
"""Tests for admin web interface report discovery helpers (shareomat.web.reports)."""

from __future__ import annotations

from pathlib import Path
from html import unescape
from io import BytesIO
import re
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.web.reports import list_billing_reports, render_report_section, report_path
from shareomat.web.state import get_state
from shareomat.web.server import _Handler


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


@pytest.mark.parametrize("suffix", ["Prüfung Mai", "Bezug+Export", "A&B#1", "Literal%20"])
@pytest.mark.parametrize("ingress_path", ["", "/api/hassio_ingress/test"])
def test_report_download_links_preserve_filename(tmp_path, suffix, ingress_path):
    _register_reports(tmp_path)
    filename = f"billing_{suffix}.json"
    payload = b"[]"
    (tmp_path / filename).write_bytes(payload)
    html = render_report_section(f"billing_{suffix}", ingress_path)
    links = re.findall(r'href="([^"]+)"', html)
    assert len(links) == 1
    url = urlsplit(unescape(links[0]))
    assert url.path == f"{ingress_path}/reports/download"
    assert not url.fragment
    assert parse_qs(url.query) == {"f": [filename]}

    statuses = []
    headers = {}
    handler = SimpleNamespace(
        send_response=statuses.append,
        send_header=lambda key, value: headers.update({key: value}),
        end_headers=lambda: None,
        wfile=BytesIO(),
    )
    _Handler._serve_download(handler, url.query)
    assert statuses == [200]
    assert handler.wfile.getvalue() == payload
    encoded_filename = headers["Content-Disposition"].split("filename*=UTF-8''", 1)[1]
    assert unquote(encoded_filename) == filename
