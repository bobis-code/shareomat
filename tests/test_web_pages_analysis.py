# -*- coding: utf-8 -*-
"""Tests for the "Analyse" admin page (shareomat.web.pages.analysis)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.community import save_community
from shareomat.database.participants import create_participant
from shareomat.database.settlement_history import save_settlement_snapshot
from shareomat.database.sqlite import init_db
from shareomat.leg_const import PARTICIPANT_TYPE_CONSUMER
from shareomat.models.billing import BillingRecord
from shareomat.models.community import Community
from shareomat.models.participant import Participant
from shareomat.web.pages import analysis as analysis_page
from shareomat.web.rendering import RequestContext
from shareomat.web.state import get_state


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    save_community(path, Community(community_id="ZEV-001", name="Test"))
    create_participant(path, Participant("cons_a", "Consumer A", PARTICIPANT_TYPE_CONSUMER))
    return path


@pytest.fixture
def runtime(tmp_path, db_path):
    for name in ("inbox", "archive", "reports", "state"):
        (tmp_path / name).mkdir()
    rt = RuntimeConfig(paths=PathConfig(
        inbox=tmp_path / "inbox", archive=tmp_path / "archive",
        reports=tmp_path / "reports", state=tmp_path / "state",
    ))
    get_state().register_runtime(rt)
    return rt


def _ctx(query: dict | None = None) -> RequestContext:
    return RequestContext(method="GET", ingress_path="", query=query or {})


def _seed_history(db_path, days_ago=1):
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(hour=23, minute=59)
    record = BillingRecord(
        participant_id="cons_a", label="Consumer A", meter_ids=["M1"],
        period_start=start, period_end=end,
        total_export_kwh=0.0, local_supplied_kwh=0.0, grid_export_kwh=0.0,
        total_import_kwh=10.0, local_received_kwh=7.0, grid_import_kwh=3.0,
        local_rate_chf=0.2, grid_rate_chf=0.3,
        local_cost_chf=1.4, grid_cost_chf=0.9, total_cost_chf=2.3,
    )
    save_settlement_snapshot(db_path, uuid.uuid4().hex, "fp-test", start, end, [record])


def test_renders_without_data(runtime) -> None:
    html = analysis_page.handle_get(_ctx())
    assert "Analyse" in html
    assert "Noch keine Abrechnungszyklen" in html


def test_renders_leg_view_with_data(runtime, db_path) -> None:
    _seed_history(db_path)
    html = analysis_page.handle_get(_ctx({"view": "leg", "range": "30d"}))
    assert "LEG-weit" in html
    assert "<svg" in html


def test_renders_participant_view_with_data(runtime, db_path) -> None:
    _seed_history(db_path)
    html = analysis_page.handle_get(_ctx({"view": "participant", "participant_id": "cons_a", "range": "30d"}))
    assert "Consumer A" in html
    assert "<svg" in html


def test_participant_view_without_participant_id_falls_back_to_leg(runtime, db_path) -> None:
    _seed_history(db_path)
    html = analysis_page.handle_get(_ctx({"view": "participant"}))
    assert "LEG-weit" in html


def test_price_overlay_toggle(runtime, db_path) -> None:
    html_without = analysis_page.handle_get(_ctx({}))
    assert "Marktpreis-Prognose (CHF/kWh)" not in html_without

    html_with = analysis_page.handle_get(_ctx({"overlay": "price"}))
    assert "Marktpreis-Prognose (CHF/kWh)" in html_with


def test_invalid_range_falls_back_to_default(runtime, db_path) -> None:
    html = analysis_page.handle_get(_ctx({"range": "bogus"}))
    assert "30 Tage" in html or "Letzte 30 Tage" in html
