# -*- coding: utf-8 -*-
"""
File: tests/test_f03_validation.py

Purpose:
    Regressions for non-finite prices and malformed interval CSV files.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from shareomat.core.pipeline.leg_parser import parse_csv
from shareomat.database import supplier_tariffs, tariffs
from shareomat.database import contract_versions, day_ahead_prices, elcom_tariffs, price_forecasts, reference_prices
from shareomat.database.sqlite import connect, init_db
from shareomat.external_data.vnb_tariff_json import VnbTariffFormatError, parse_vnb_tariff_json
from shareomat.models.external_data import SupplierTariff
from shareomat.models.external_data import DayAheadPricePoint, PriceForecast, ReferencePrice
from shareomat.models.contract import ContractVersion
from shareomat.external_data.elcom import ElcomTariffComponents
from shareomat.models.tariff import Tariff
from shareomat.web.pages import tariffs as page
from shareomat.web.rendering import FormError, RequestContext, form_decimal
from shareomat.web.state import get_state
from tests.test_leg_runner_overlaps import env, _outputs, _row, _write, _PRODUCER, _CONSUMER  # noqa: F401


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    get_state().register_db(path)
    return path


def _tariff(**changes):
    return replace(Tariff(Decimal("0.2"), Decimal("0"), Decimal("0.1"),
                          valid_from=date(2027, 1, 1)), **changes)


def _supplier(**changes):
    return replace(SupplierTariff("Testnetz", date(2027, 1, 1), product="Testprodukt",
                                   energy_rate_ht_chf_kwh=Decimal("0.1")), **changes)


def _json(price="0.1", **entry_changes):
    entry = {"tariffType": "electricity", "tariffName": "Testprodukt", "startDate": "2027-01-01",
             "endDate": "2027-12-31", "prices": {"energy": [{"price": price, "priceUnit": "CHF/kWh"}]}}
    entry.update(entry_changes)
    return {"dsoName": "Testnetz", "tariffs": [entry]}


@pytest.mark.parametrize("raw", ["NaN", "sNaN", "Infinity", "-Infinity", "", None, True, [], {}])
def test_form_decimal_rejects_invalid_values(raw):
    with pytest.raises(FormError):
        form_decimal(RequestContext(method="POST", ingress_path="", form={"price": raw}), "price", label="Preis")


@pytest.mark.parametrize("raw,expected", [("0", "0"), ("-0,25", "-0.25"), (" 0.125 ", "0.125"),
                                         ("1e3", "1000"), ("1e100", "1e100")])
def test_form_decimal_preserves_finite_signed_values(raw, expected):
    assert form_decimal(RequestContext(method="POST", ingress_path="", form={"p": raw}),
                        "p", label="Preis") == Decimal(expected)


@pytest.mark.parametrize("field", ["local_rate_chf_kwh", "grid_rate_chf_kwh", "feed_in_rate_chf_kwh",
                                 "admin_fee_chf_kwh", "local_rate_nt_chf_kwh", "feed_in_rate_nt_chf_kwh"])
@pytest.mark.parametrize("raw", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_internal_price_fields_reject_non_finite_before_write(db, field, raw):
    with pytest.raises(ValueError):
        tariffs.create_tariff(db, _tariff(**{field: raw}))
    assert tariffs.list_tariffs(db) == []


@pytest.mark.parametrize("raw", ["", None, True, [], {}, "1,2", Decimal("1e1000"), Decimal("1e-1000")])
def test_internal_price_types_and_float_overflow_are_controlled(db, raw):
    with pytest.raises(ValueError):
        tariffs.create_tariff(db, _tariff(local_rate_chf_kwh=raw))
    assert tariffs.list_tariffs(db) == []


def test_finite_zero_negative_and_large_rates_round_trip(db):
    saved = tariffs.create_tariff(db, _tariff(local_rate_chf_kwh=Decimal("-0.01"),
                                             feed_in_rate_chf_kwh=Decimal("0")))
    assert saved.local_rate_chf_kwh == Decimal("-0.01")
    large = supplier_tariffs.create_supplier_tariff(db, _supplier(base_price_chf_year=Decimal("1e1000"),
                                                                  energy_rate_ht_chf_kwh=Decimal("-0.1")))
    assert large.base_price_chf_year == Decimal("1e1000")
    assert large.energy_rate_ht_chf_kwh == Decimal("-0.1")


@pytest.mark.parametrize("operation", ["create", "update"])
def test_reversed_internal_period_rejected(db, operation):
    original = tariffs.create_tariff(db, _tariff())
    bad = _tariff(valid_until=date(2026, 12, 31))
    with pytest.raises(ValueError):
        if operation == "create":
            tariffs.create_tariff(db, bad)
        else:
            tariffs.update_tariff(db, original.id, bad)
    assert tariffs.get_tariff(db, original.id) == original


@pytest.mark.parametrize("field", ["energy_rate_ht_chf_kwh", "energy_rate_nt_chf_kwh", "grid_rate_ht_chf_kwh",
                                 "grid_rate_nt_chf_kwh", "base_price_chf_year", "metering_price_chf_year",
                                 "feed_in_rate_chf_kwh", "hkn_rate_chf_kwh"])
def test_supplier_fields_are_validated_before_replacement(db, field):
    saved = supplier_tariffs.create_supplier_tariff(db, _supplier())
    with pytest.raises(ValueError):
        supplier_tariffs.replace_supplier_tariffs(db, saved.source, saved.valid_from,
                                                 [_supplier(**{field: Decimal("NaN")})])
    assert supplier_tariffs.list_supplier_tariffs(db) == [saved]


@pytest.mark.parametrize("raw", ["NaN", "sNaN", "Infinity", "-Infinity", "", None, True, [], {}, "0,1"])
def test_json_invalid_prices_raise_format_error(raw):
    with pytest.raises(VnbTariffFormatError):
        parse_vnb_tariff_json(_json(raw))


@pytest.mark.parametrize("data", [{"dsoName": "Test", "tariffs": None},
                                 {"dsoName": "Test", "tariffs": [None]},
                                 _json(prices={"energy": [None]}),
                                 _json(prices={"energy": [{"priceUnit": "CHF/kWh"}]}),
                                 _json(endDate="2026-01-01")])
def test_json_malformed_structure_and_period_raise_format_error(data):
    with pytest.raises(VnbTariffFormatError):
        parse_vnb_tariff_json(data)


def test_json_zero_base_is_not_missing_and_negative_energy_is_preserved():
    data = _json("-0.01")
    data["tariffs"][0]["prices"]["base"] = {"price": 0, "priceUnit": "CHF/M"}
    _, [product] = parse_vnb_tariff_json(data)
    assert product.energy_rate_ht_chf_kwh == Decimal("-0.01")
    assert product.base_price_chf_year == Decimal("0")


def test_invalid_persisted_tariff_is_rejected_without_repair(db):
    saved = tariffs.create_tariff(db, _tariff())
    with connect(db) as conn:
        with conn:
            conn.execute("UPDATE tariffs SET grid_rate_chf_kwh = 'NaN' WHERE id = ?", (saved.id,))
    with pytest.raises(ValueError):
        tariffs.get_tariff(db, saved.id)
    with connect(db) as conn:
        assert conn.execute("SELECT grid_rate_chf_kwh FROM tariffs WHERE id = ?", (saved.id,)).fetchone()[0] == "NaN"


@pytest.mark.parametrize("segments,form", [(["new"], {"name": "Test", "local_rate_chf_kwh": "0.1",
                                                    "grid_rate_chf_kwh": "0", "feed_in_rate_chf_kwh": "0",
                                                    "valid_from": "2027-01-01", "valid_until": "2026-01-01"}),
                                        (["vnb", "new"], {"source": "Test", "product": "Test",
                                                           "energy_rate_ht": "NaN", "valid_from": "2027-01-01"})])
def test_tariff_page_reports_controlled_form_errors(db, segments, form):
    with pytest.raises(FormError):
        page.handle_post(RequestContext(method="POST", ingress_path="", segments=segments, form=form))


HEADER = "timestamp,meter_id,value_kwh,direction,quality\n"
GOOD = "2027-01-01T00:00:00Z,TEST_METER,1,import,valid\n"
NEXT = "2027-01-01T00:15:00Z,TEST_METER,2,import,valid\n"


@pytest.mark.parametrize("bad", ["2027-01-01T00:00:00Z,TEST_METER\n",
                                "2027-01-01T00:00:00Z,TEST_METER,1\n",
                                "2027-01-01T00:00:00Z,TEST_METER,1,import\n",
                                "2027-01-01T00:00:00Z,TEST_METER,,import,valid\n",
                                "invalid,TEST_METER,1,import,valid\n",
                                "2027-01-01T00:00:00,TEST_METER,1,import,valid\n",
                                "2027-01-01T00:00:00Z,TEST_METER,NaN,import,valid\n",
                                "2027-01-01T00:00:00Z,TEST_METER,Infinity,import,valid\n",
                                "2027-01-01T00:00:00Z,TEST_METER,1,import,valid,extra\n"])
def test_bad_csv_row_is_reported_without_losing_later_good_rows(tmp_path, caplog, bad):
    path = tmp_path / "test.csv"
    path.write_text(HEADER + bad + GOOD + NEXT, encoding="utf-8")
    errors = []
    result = parse_csv(path, errors=errors)
    assert [r.value_kwh for r in result] == [1, 2]
    assert len(errors) == 1 and "Zeile 2" in errors[0]
    assert "test.csv:2" in caplog.text


@pytest.mark.parametrize("header", ["timestamp,meter_id,value_kwh\n", "timestamp,value_kwh,direction\n",
                                   "timestamp,meter_id,value_kwh,direction,direction\n", ""])
def test_csv_invalid_header_even_without_data_is_reported(tmp_path, header):
    path = tmp_path / "test.csv"
    path.write_text(header, encoding="utf-8")
    errors = []
    assert parse_csv(path, errors=errors) == []
    assert len(errors) == 1


def test_csv_unclosed_quote_is_reported(tmp_path):
    path = tmp_path / "test.csv"
    path.write_text(HEADER + GOOD + '"unterminated', encoding="utf-8")
    errors = []
    assert len(parse_csv(path, errors=errors)) == 1
    assert errors


def test_csv_identical_and_conflicting_duplicates_keep_existing_policy(tmp_path):
    path = tmp_path / "test.csv"
    path.write_text(HEADER + GOOD + GOOD + GOOD.replace(",1,import", ",3,import") + NEXT, encoding="utf-8")
    errors = []
    assert [r.value_kwh for r in parse_csv(path, errors=errors)] == [1, 2]
    assert len(errors) == 1 and "Doppelzeile" in errors[0]


@pytest.mark.parametrize("kind", ["reference", "forecast", "day_ahead", "elcom"])
@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_other_price_stores_reject_non_finite(db, kind, value):
    with pytest.raises(ValueError):
        _save_price(db, kind, value)
    table = {"reference": "reference_prices", "forecast": "price_forecasts",
             "day_ahead": "day_ahead_prices", "elcom": "elcom_tariffs"}[kind]
    with connect(db) as conn:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def _save_price(db, kind, value):
    if kind == "reference":
        return reference_prices.save_reference_price(db, ReferencePrice("pv", date(2027, 1, 1),
                                                                        date(2027, 3, 31), value, "Test"))
    if kind == "forecast":
        return price_forecasts.save_price_forecast(db, PriceForecast(date(2027, 1, 1), date(2027, 1, 31),
                                                                    value, ["test"]))
    if kind == "day_ahead":
        return day_ahead_prices.save_day_ahead_prices(db, [DayAheadPricePoint(
            datetime(2027, 1, 1, tzinfo=timezone.utc), value)])
    return elcom_tariffs.save_elcom_tariffs(db, "TEST", 2027, [ElcomTariffComponents(
        "TEST", value, Decimal("0"), Decimal("0"), Decimal("0"), value, Decimal("0"), None, "Test")])


@pytest.mark.parametrize("kind", ["reference", "forecast", "day_ahead", "elcom"])
@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("0")])
def test_other_price_stores_preserve_signed_finite_values(db, kind, value):
    _save_price(db, kind, value)
    if kind == "reference":
        assert reference_prices.list_reference_prices(db)[0].price_chf_kwh == value
    elif kind == "forecast":
        assert price_forecasts.list_price_forecasts(db)[0].forecast_price_chf_kwh == value
    elif kind == "day_ahead":
        assert day_ahead_prices.list_day_ahead_prices(db)[0].price_chf_kwh == value
    else:
        assert elcom_tariffs.list_elcom_tariffs(db)[0][2].energy_chf_kwh == value


@pytest.mark.parametrize("field", ["vnb_reference_price_chf_kwh", "price_reduction_chf_kwh",
                                 "vnb_reference_price_nt_chf_kwh", "price_reduction_nt_chf_kwh",
                                 "local_rate_chf_kwh", "local_rate_nt_chf_kwh", "feed_in_rate_chf_kwh",
                                 "feed_in_rate_nt_chf_kwh", "admin_fee_chf_kwh"])
def test_contract_drafts_cannot_persist_non_finite_price_fields(db, field):
    version = ContractVersion("TEST", 0, "draft", "", **{field: Decimal("NaN")})
    with pytest.raises(contract_versions.ContractWorkflowError):
        contract_versions.save_draft_version(db, version)


def test_unfinished_contract_draft_keeps_existing_date_and_zero_rules(db):
    version = ContractVersion("TEST", 0, "draft", "", valid_from=date(2027, 2, 1),
                              valid_until=date(2027, 1, 1))
    saved = contract_versions.save_draft_version(db, version)
    assert saved.local_rate_chf_kwh == Decimal("0")
    assert saved.valid_until < saved.valid_from
    with pytest.raises(contract_versions.ContractWorkflowError):
        contract_versions.publish_version(db, saved.id)


@pytest.mark.parametrize("raw", ["0.1", "0,1", "+1.2e-2", "-0", "0"])
def test_manual_supplier_formats_and_signed_values_are_preserved(db, raw):
    page.handle_post(RequestContext(method="POST", ingress_path="", segments=["vnb", "new"], form={
        "source": "Testnetz", "product": "Test", "energy_rate_ht": raw, "valid_from": "2027-01-01"}))
    assert supplier_tariffs.list_supplier_tariffs(db)[0].energy_rate_ht_chf_kwh == Decimal(raw.replace(",", ".")) / 100


@pytest.mark.parametrize("raw", ["1,234.56", "1.234,56", "not-a-number"])
def test_ambiguous_manual_decimal_formats_are_rejected(raw):
    with pytest.raises(FormError):
        form_decimal(RequestContext(method="POST", ingress_path="", form={"p": raw}), "p", label="Preis")


def test_legacy_mpid_and_missing_optional_quality_remain_supported(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text("timestamp,mpid,value_kwh,direction\n2027-01-01T00:00:00Z,TEST_METER,1,import\n", encoding="utf-8")
    errors = []
    [reading] = parse_csv(path, errors=errors)
    assert reading.meter_id == "TEST_METER" and reading.quality == "valid"
    assert errors == []


@pytest.mark.parametrize("bad", ["timestamp,meter_id,value_kwh\n", HEADER + "bad,TEST_METER,1,import\n",
                                HEADER + '"unterminated', HEADER + "bad,TEST_METER,1,import,valid\n"])
def test_invalid_file_never_marks_automatic_batch_successful(env, bad):
    from shareomat.core.collector.leg_import import scan_inbox
    from shareomat.core.collector.leg_storage import is_processed
    from shareomat.core.leg_runner import run

    _write(env, "good.csv", [_row(_PRODUCER, 1, "export"), _row(_CONSUMER, 1)])
    path = env.config.paths.inbox / "bad.csv"
    path.write_text(bad, encoding="utf-8")
    files = scan_inbox(env.config.paths.inbox)
    before = _outputs(env)
    with pytest.raises(ValueError, match="bad.csv"):
        run(env.config, mqtt_client=env.mqtt, db_path=env.db)
    assert _outputs(env) == before
    assert all(not is_processed(env.config.paths.state, item.sha256) for item in files)
    assert all(item.path.exists() for item in files)
    env.mqtt.publish.assert_not_called()


@pytest.mark.parametrize("bad", ["timestamp,meter_id,value_kwh\n", HEADER + "bad,TEST_METER,1,import,valid\n"])
def test_file_with_only_errors_blocks_interactive_billing_even_with_other_good_data(env, bad):
    from shareomat.config import RuntimeConfig
    from shareomat.database.billing import compute_billing_preview

    _write(env, "good.csv", [_row(_PRODUCER, 1, "export"), _row(_CONSUMER, 1)])
    (env.config.paths.inbox / "bad.csv").write_text(bad, encoding="utf-8")
    preview = compute_billing_preview(env.db, RuntimeConfig(paths=env.config.paths),
                                      date(2026, 11, 3), date(2026, 11, 3))
    assert any("bad.csv" in issue for issue in preview.availability.hard_issues)


@pytest.mark.parametrize("raw", ["abc", '"1,25"', "-1", "1e1000"])
def test_csv_bad_number_formats_are_reported_and_later_rows_survive(tmp_path, raw):
    path = tmp_path / "test.csv"
    path.write_text(HEADER + f"2027-01-01T00:00:00Z,TEST_METER,{raw},import,valid\n" + NEXT, encoding="utf-8")
    errors = []
    assert [row.value_kwh for row in parse_csv(path, errors=errors)] == [2]
    assert len(errors) == 1


@pytest.mark.parametrize("kind", ["reference", "forecast"])
def test_reference_and_forecast_reject_reversed_periods(db, kind):
    with pytest.raises(ValueError):
        if kind == "reference":
            reference_prices.save_reference_price(db, ReferencePrice("pv", date(2027, 2, 1),
                                                                      date(2027, 1, 1), Decimal("0"), "Test"))
        else:
            price_forecasts.save_price_forecast(db, PriceForecast(date(2027, 2, 1), date(2027, 1, 1),
                                                                  Decimal("0"), ["test"]))


@pytest.mark.parametrize("field", ["local_rate_nt_chf_kwh", "feed_in_rate_nt_chf_kwh"])
def test_empty_persisted_optional_rate_is_not_treated_as_missing(db, field):
    saved = tariffs.create_tariff(db, _tariff())
    with connect(db) as conn:
        with conn:
            conn.execute(f"UPDATE tariffs SET {field} = '' WHERE id = ?", (saved.id,))
    with pytest.raises(ValueError):
        tariffs.get_tariff(db, saved.id)


@pytest.mark.parametrize("raw", ["NaN", "Infinity", "-Infinity", None, True, ""])
def test_json_invalid_base_price_is_rejected(raw):
    data = _json()
    data["tariffs"][0]["prices"]["base"] = {"price": raw, "priceUnit": "CHF/M"}
    with pytest.raises(VnbTariffFormatError):
        parse_vnb_tariff_json(data)


def test_invalid_last_product_does_not_replace_existing_json_import(db, monkeypatch):
    saved = supplier_tariffs.create_supplier_tariff(db, _supplier())
    data = _json()
    data["tariffs"].append(_json("NaN")["tariffs"][0])
    monkeypatch.setattr(page, "fetch_vnb_tariff_json", lambda url: data)
    with pytest.raises(FormError):
        page.handle_post(RequestContext(method="POST", ingress_path="", segments=["vnb", "import"],
                                        form={"url": "https://example.invalid/test.json"}))
    assert supplier_tariffs.list_supplier_tariffs(db) == [saved]


def test_contract_current_reference_fields_do_not_bypass_price_validation():
    from shareomat.web.pages.contract import _reference_from_form

    ctx = RequestContext(method="POST", ingress_path="", form={"reference_tariff_id": "current",
                                                               "current_reference_price": "NaN"})
    with pytest.raises(FormError):
        _reference_from_form(ctx)


@pytest.mark.parametrize("rate,fee,allowed", [("0.10", "0", True), ("0", "0", True),
                                            ("-0.1", "0", False), ("0.1", "-0.1", False),
                                            ("0.1", "0.2", False)])
def test_existing_contract_price_sign_rules_are_preserved(rate, fee, allowed):
    from shareomat.web.pages.contract import _compute_rates

    if allowed:
        assert _compute_rates(local_rate=Decimal(rate), admin_fee=Decimal(fee)) == Decimal(rate) - Decimal(fee)
    else:
        with pytest.raises(FormError):
            _compute_rates(local_rate=Decimal(rate), admin_fee=Decimal(fee))


@pytest.mark.parametrize("kind", ["internal", "supplier"])
def test_required_tariff_start_date_has_a_controlled_error(db, kind):
    with pytest.raises(ValueError, match="Datum fehlt"):
        if kind == "internal":
            tariffs.create_tariff(db, _tariff(valid_from=None))
        else:
            supplier_tariffs.create_supplier_tariff(db, _supplier(valid_from=None))
