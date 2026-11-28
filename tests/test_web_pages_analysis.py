# -*- coding: utf-8 -*-
"""Tests for the "Analyse" routing (shareomat.web.pages.analysis).

"Analyse → Übersicht" showed the results of the automatic CSV settlement runs
(settlement_history) and went with the CSV path in 0.6.34 (ADR-0012). /analysis
now opens "Summen & Auswertung" on the official data; its own tests are in
tests/test_energy_analysis.py.
"""

from __future__ import annotations

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.community import save_community
from shareomat.database.sqlite import init_db
from shareomat.models.community import Community
from shareomat.web.navigation import NAV_GROUPS
from shareomat.web.pages import analysis as analysis_page
from shareomat.web.pages import energy_analysis
from shareomat.web.rendering import RequestContext
from shareomat.web.state import get_state


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    save_community(path, Community(community_id="ZEV-001", name="Test"))
    get_state().register_runtime(RuntimeConfig(paths=PathConfig(
        inbox=tmp_path / "inbox", archive=tmp_path / "archive", reports=tmp_path / "reports", state=tmp_path / "state")))
    return path


def _ctx(segments: list[str]) -> RequestContext:
    return RequestContext(method="GET", ingress_path="", segments=segments)


def test_analysis_opens_the_sums_on_the_official_data(db_path) -> None:
    assert analysis_page.handle_get(_ctx([])) == energy_analysis.sums_page(_ctx([]))
    assert analysis_page.handle_get(_ctx(["sums"])) == energy_analysis.sums_page(_ctx(["sums"]))


def test_values_and_unknown_pages(db_path) -> None:
    assert analysis_page.handle_get(_ctx(["values"])) == energy_analysis.values_page(_ctx(["values"]))
    assert "Seite nicht gefunden" in analysis_page.handle_get(_ctx(["uebersicht"]))


def test_menu_has_no_overview_of_csv_runs() -> None:
    routes = {route for group in NAV_GROUPS for route in group.routes}
    assert "analysis" not in routes and "reports" not in routes
    assert {"analysis_sums", "analysis_values", "meter_data"} <= routes
