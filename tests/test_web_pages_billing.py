# -*- coding: utf-8 -*-
"""Tests for the "Abrechnungen" admin page (shareomat.web.pages.billing)."""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.community import save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.leg_const import (
    METER_ROLE_CONSUMER,
    METER_ROLE_PRODUCER,
    PARTICIPANT_TYPE_CONSUMER,
    PARTICIPANT_TYPE_PRODUCER,
)
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff
from shareomat.web.pages import billing as billing_page
from shareomat.web.rendering import FormError, Redirect, RequestContext
from shareomat.web.state import get_state
from datetime import date


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    return path


@pytest.fixture
def runtime(tmp_path, db_path):
    for name in ("inbox", "archive", "reports", "state"):
        (tmp_path / name).mkdir()
    runtime = RuntimeConfig(paths=PathConfig(
        inbox=tmp_path / "inbox", archive=tmp_path / "archive",
        reports=tmp_path / "reports", state=tmp_path / "state",
    ))
    get_state().register_runtime(runtime)

    save_community(db_path, Community(community_id="ZEV-001", name="Test"))
    create_participant(db_path, Participant("solar", "Solar PV", PARTICIPANT_TYPE_PRODUCER))
    create_participant(db_path, Participant("cons_a", "Consumer A", PARTICIPANT_TYPE_CONSUMER))
    create_meter(db_path, Meter("M1", "solar", "PV Meter", METER_ROLE_PRODUCER))
    create_meter(db_path, Meter("M2", "cons_a", "Meter A", METER_ROLE_CONSUMER))
    create_tariff(db_path, Tariff(
        local_rate_chf_kwh=Decimal("0.12"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), valid_from=date(2020, 1, 1),
    ))

    inbox = tmp_path / "inbox"
    with (inbox / "readings.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        writer.writerow(["2027-07-01T12:00:00+00:00", "M1", "1.0", "export"])
        writer.writerow(["2027-07-01T12:00:00+00:00", "M2", "0.6", "import"])
    return runtime


def test_billing_route_is_no_longer_a_placeholder():
    from shareomat.web.navigation import PLACEHOLDER_ROUTES
    assert "billing" not in PLACEHOLDER_ROUTES


def test_list_page_renders_empty_state(db_path):
    html = billing_page.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "Noch keine Abrechnungen" in html


def test_new_page_renders_period_form(db_path):
    html = billing_page.handle_get(RequestContext(method="GET", ingress_path="", segments=["new"]))
    assert "period_start" in html


def test_preview_requires_valid_dates(db_path, runtime):
    ctx = RequestContext(
        method="GET", ingress_path="", segments=["preview"],
        query={"period_start": "not-a-date", "period_end": "2027-07-31"},
    )
    with pytest.raises(FormError):
        billing_page.handle_get(ctx)


def test_full_workflow_save_release(db_path, runtime):
    csrf = get_state().csrf_token

    preview_ctx = RequestContext(
        method="GET", ingress_path="", segments=["preview"],
        query={"period_start": "2027-07-01", "period_end": "2027-07-31"},
    )
    html = billing_page.handle_get(preview_ctx)
    assert "Consumer A" in html

    save_ctx = RequestContext(
        method="POST", ingress_path="", segments=["save"],
        form={"csrf_token": csrf, "period_start": "2027-07-01", "period_end": "2027-07-31"},
    )
    with pytest.raises(Redirect) as exc_info:
        billing_page.handle_post(save_ctx)
    location = exc_info.value.location
    run_id = location.rstrip("/").split("/")[-1]

    detail_html = billing_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[run_id]))
    assert "Freigeben" in detail_html

    release_ctx = RequestContext(
        method="POST", ingress_path="", segments=[run_id, "release"], form={"csrf_token": csrf},
    )
    with pytest.raises(Redirect):
        billing_page.handle_post(release_ctx)

    released_html = billing_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[run_id]))
    assert "Stornieren" in released_html
    assert "Freigegeben am" in released_html
    assert '<button type="submit">Freigeben</button>' not in released_html
