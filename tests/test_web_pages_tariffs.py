# -*- coding: utf-8 -*-
"""Tests for the "Tarife" page: grid operator reference products vs. read-only LEG prices."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from shareomat.database.supplier_tariffs import list_supplier_tariffs
from shareomat.database.sqlite import init_db
from shareomat.external_data.vnb_tariff_json import VnbTariffFormatError, parse_vnb_tariff_json
from shareomat.web.pages import tariffs
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state

# Own minimal sample in the published DSO tariff JSON format (structure as in EBL 2027).
_SAMPLE_JSON = Path(__file__).resolve().parent / "fixtures" / "vnb_tariff_sample.json"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    return path


def _post(segments, form=None) -> RequestContext:
    form = dict(form or {})
    form["csrf_token"] = get_state().csrf_token
    ctx = RequestContext(method="POST", ingress_path="", form=form, query={})
    ctx.segments = segments
    return ctx


def _get() -> RequestContext:
    ctx = RequestContext(method="GET", ingress_path="", form={}, query={})
    ctx.segments = []
    return ctx


# ── JSON parser ─────────────────────────────────────────────────────────────


def test_parse_energy_products():
    operator, products = parse_vnb_tariff_json(json.loads(_SAMPLE_JSON.read_text(encoding="utf-8")))
    assert operator == "Beispiel-Netz"
    by_name = {p.product: p for p in products if p.valid_from == date(2027, 1, 1)}
    assert set(by_name) == {"Standard Energie Blau 2027", "Standard Energie Grau 2027", "Flexa Energie Grau 2027"}
    grau = by_name["Standard Energie Grau 2027"]
    assert grau.energy_rate_ht_chf_kwh == Decimal("0.096")
    assert grau.energy_rate_nt_chf_kwh is None
    flexa = by_name["Flexa Energie Grau 2027"]
    assert (flexa.energy_rate_ht_chf_kwh, flexa.energy_rate_nt_chf_kwh) == (Decimal("0.106"), Decimal("0.077"))
    # Only energy products — never grid usage, metering or levies.
    assert all("Messtarif" not in p.product for p in products)


def test_parse_rejects_unknown_structure():
    with pytest.raises(VnbTariffFormatError):
        parse_vnb_tariff_json({"foo": []})


# ── Page ────────────────────────────────────────────────────────────────────


def test_import_stores_products_and_reimport_replaces_them(db_path, monkeypatch):
    data = json.loads(_SAMPLE_JSON.read_text(encoding="utf-8"))
    monkeypatch.setattr(tariffs, "fetch_vnb_tariff_json", lambda url: data)

    tariffs.handle_post(_post(["vnb", "import"], {"url": "https://example.invalid/EBL.json"}))
    first = len(list_supplier_tariffs(db_path))
    tariffs.handle_post(_post(["vnb", "import"], {"url": "https://example.invalid/EBL.json"}))

    assert first == 3
    assert len(list_supplier_tariffs(db_path)) == first  # idempotent per operator + year


def test_manual_product_in_rappen_is_stored_in_chf(db_path):
    tariffs.handle_post(_post(["vnb", "new"], {
        "source": "EBL", "product": "EBL Grau 2027", "energy_rate_ht": "9,60", "energy_rate_nt": "",
        "valid_from": "2027-01-01",
    }))
    [stored] = list_supplier_tariffs(db_path)
    assert stored.energy_rate_ht_chf_kwh == Decimal("0.096")
    assert stored.energy_rate_nt_chf_kwh is None

    html = tariffs.handle_get(_get())
    assert "EBL Grau 2027" in html
    assert "9.60" in html


def test_toggle_and_delete_product(db_path):
    tariffs.handle_post(_post(["vnb", "new"], {
        "source": "EBL", "product": "EBL Blau 2027", "energy_rate_ht": "9.8", "valid_from": "2027-01-01",
    }))
    [stored] = list_supplier_tariffs(db_path)

    tariffs.handle_post(_post(["vnb", str(stored.id), "toggle"]))
    assert list_supplier_tariffs(db_path)[0].active is False

    tariffs.handle_post(_post(["vnb", str(stored.id), "delete"]))
    assert list_supplier_tariffs(db_path) == []


def test_manual_product_requires_valid_from(db_path):
    with pytest.raises(FormError):
        tariffs.handle_post(_post(["vnb", "new"], {"source": "EBL", "product": "X", "energy_rate_ht": "9"}))


def test_leg_prices_are_read_only_in_the_list(db_path):
    html = tariffs.handle_get(_get())
    assert "LEG-Preise (aus dem Vertrag)" in html
    assert "+ Tarif anlegen" not in html
