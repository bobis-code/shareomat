# -*- coding: utf-8 -*-
"""Tests for the "Externe Daten" admin page (shareomat.web.pages.external_data)."""

from __future__ import annotations

import json

import pytest

from shareomat.database.data_imports import list_recent_imports
from shareomat.database.external_settings import get_external_data_settings
from shareomat.database.sqlite import init_db
from shareomat.web.pages import external_data
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    return path


def test_get_renders_page(db_path):
    html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "Externe Daten" in html
    assert "ENTSO-E" in html


def test_post_token_saves_setting(db_path):
    ctx = RequestContext(method="POST", ingress_path="", segments=["token"], form={"entsoe_api_token": "  my-token  "})
    assert external_data.handle_post(ctx) is None
    assert get_external_data_settings(db_path).entsoe_api_token == "my-token"


def test_post_elcom_invalid_number_raises_form_error(db_path):
    ctx = RequestContext(
        method="POST", ingress_path="", segments=["elcom"],
        form={"municipality_bfs_number": "not-a-number", "year": "2024"},
    )
    with pytest.raises(FormError):
        external_data.handle_post(ctx)


def test_post_elcom_logs_error_on_fetch_failure(db_path, monkeypatch):
    from shareomat.external_data import elcom

    def fake_fail(*args, **kwargs):
        raise elcom.ElcomFetchError("simulated network failure")

    monkeypatch.setattr(elcom, "download_elcom_tariffs", fake_fail)
    monkeypatch.setattr("shareomat.web.pages.external_data.download_elcom_tariffs", fake_fail)

    ctx = RequestContext(
        method="POST", ingress_path="", segments=["elcom"],
        form={"municipality_bfs_number": "5250", "year": "2024"},
    )
    assert external_data.handle_post(ctx) is None

    imports = list_recent_imports(db_path)
    assert imports[0].source == "ElCom"
    assert imports[0].status == "error"
    assert "simulated network failure" in imports[0].detail


def test_post_unknown_action_raises_form_error(db_path):
    ctx = RequestContext(method="POST", ingress_path="", segments=["nonsense"], form={})
    with pytest.raises(FormError):
        external_data.handle_post(ctx)
