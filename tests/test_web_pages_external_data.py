# -*- coding: utf-8 -*-
"""Tests for the "Externe Daten" admin page (shareomat.web.pages.external_data)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from shareomat.database.data_imports import list_recent_imports
from shareomat.database.elcom_tariffs import list_elcom_tariffs
from shareomat.database.exchange_rates import list_exchange_rates
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


def test_post_settings_saves_token_and_municipality(db_path):
    ctx = RequestContext(
        method="POST", ingress_path="", segments=["settings"],
        form={"entsoe_api_token": "  my-token  ", "municipality_bfs_number": "5250"},
    )
    assert external_data.handle_post(ctx) is None
    settings = get_external_data_settings(db_path)
    assert settings.entsoe_api_token == "my-token"
    assert settings.municipality_bfs_number == "5250"


def test_elcom_fetch_remembers_municipality_as_new_default(db_path, monkeypatch):
    monkeypatch.setattr("shareomat.web.pages.external_data.download_elcom_tariffs", lambda *a, **k: [])

    assert get_external_data_settings(db_path).municipality_bfs_number == ""
    ctx = RequestContext(
        method="POST", ingress_path="", segments=["elcom"],
        form={"municipality_bfs_number": "261", "year": "2024"},
    )
    external_data.handle_post(ctx)
    assert get_external_data_settings(db_path).municipality_bfs_number == "261"


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


def test_elcom_fetch_persists_results_for_display(db_path, monkeypatch):
    from shareomat.external_data.elcom import ElcomTariffComponents

    fake_tariffs = [ElcomTariffComponents(
        category="H4", energy_chf_kwh=Decimal("0.13456"), grid_chf_kwh=Decimal("0.14108"),
        aidfee_chf_kwh=Decimal("0.023"), community_fees_chf_kwh=Decimal("0.021"),
        total_chf_kwh=Decimal("0.32"), fixcosts_chf_year=Decimal("104"),
        operator_iri=None, operator_name="AGE SA",
    )]
    monkeypatch.setattr(
        "shareomat.web.pages.external_data.download_elcom_tariffs", lambda *a, **k: fake_tariffs,
    )

    ctx = RequestContext(
        method="POST", ingress_path="", segments=["elcom"],
        form={"municipality_bfs_number": "5250", "year": "2024"},
    )
    external_data.handle_post(ctx)

    stored = list_elcom_tariffs(db_path, municipality_bfs_number="5250")
    assert len(stored) == 1
    assert stored[0][2].energy_chf_kwh == Decimal("0.13456")

    html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "AGE SA" in html
    assert "0.13456" in html


def test_snb_fetch_persists_results_for_display(db_path, monkeypatch):
    from shareomat.external_data.market_forecast import ExchangeRate

    fake_rates = [ExchangeRate(period="2026-01", rate_chf_per_eur=Decimal("0.92732"))]
    monkeypatch.setattr(
        "shareomat.web.pages.external_data.download_exchange_rates", lambda *a, **k: fake_rates,
    )

    ctx = RequestContext(
        method="POST", ingress_path="", segments=["snb"],
        form={"from_period": "2026-01", "to_period": "2026-01"},
    )
    external_data.handle_post(ctx)

    stored = list_exchange_rates(db_path, currency="EUR")
    assert len(stored) == 1
    assert stored[0].rate_chf_per_eur == Decimal("0.92732")

    html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "0.92732" in html
