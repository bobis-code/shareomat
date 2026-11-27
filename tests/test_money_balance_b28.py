# -*- coding: utf-8 -*-
"""B28 (0.6.31): the money balance before documents are issued accounts for the admin fee.

Until 0.6.30 issue_documents() demanded "sum of invoices = sum of credits". With an admin fee
(Bezugspreis = Produzentenvergütung + Verwaltungsgebühr) the invoices are higher by the fee, so no
document could ever be issued. The balance is now exact: invoices = rounded consumer amounts,
credits = rounded producer amounts, consumer - producer amounts = admin fee + grid-operator
rounding. All values synthetic; official SDAT path (the CSV path goes with the rebuild).
"""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.core.collector.sdat_import import import_sdat_file
from shareomat.database import billing, invoicing
from shareomat.database.community import save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.settings import get_operation_settings, save_operation_settings
from shareomat.database.sqlite import connect, init_db
from shareomat.database.tariffs import create_tariff
from shareomat.leg_const import METER_DATA_SOURCE_SDAT_LEG
from shareomat.models.community import Community
from shareomat.models.invoicing import PaymentProfile
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff
from shareomat.web.pages.invoices import render_document
from shareomat.web.state import get_state
from tests.sdat_fixtures import RECEIVER_EIC, Document, Series, build, eic

DAY = date(2026, 12, 1)
DAY_START = datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)      # 01.12.2026 00:00 local
MONTH_END = datetime(2026, 12, 31, 23, 0, tzinfo=timezone.utc)
SLOTS = 96
GRID_AREA = eic("10YCH-TESTAREA0")
ISSUED = date(2027, 1, 10)


def _mpid(n: int) -> str:
    return f"CH10000000000000000000000000000{n:02d}"


C1, C2, PROSUMER, P1, P2 = _mpid(11), _mpid(12), _mpid(13), _mpid(21), _mpid(22)


def _cent(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@pytest.fixture
def leg(tmp_path):
    db = tmp_path / "shareomat.db"
    init_db(db)
    save_community(db, Community(community_id="SYNTH-LEG", name="Synthetische Test-LEG", address_line="Testweg 1",
                                 postal_code="9999", city="Testdorf", eic_code=RECEIVER_EIC))
    people = (("c1", C1, "consumer"), ("c2", C2, "consumer"), ("pro", PROSUMER, "producer_consumer"),
              ("p1", P1, "producer"), ("p2", P2, "producer"))
    for pid, mpid, role in people:
        create_participant(db, Participant(pid, f"Test {pid}", email=f"{pid}@example.ch", address_line="Testweg 2",
                                           postal_code="9999", city="Testdorf"))
        create_meter(db, Meter(mpid, pid, f"Zähler {pid}", role))
    save_operation_settings(db, replace(get_operation_settings(db), meter_data_source=METER_DATA_SOURCE_SDAT_LEG))
    invoicing.save_payment_profile(db, PaymentProfile(payee="Test-LEG Konto", street="Testweg", building="1",
                                                      postal_code="9999", city="Testdorf",
                                                      iban="CH93 0076 2011 6238 5295 7", contact_email="leg@example.ch"))
    invoicing.confirm_payment_profile(db, version=invoicing.get_payment_profile(db).version)
    paths = {}
    for name in ("inbox", "archive", "reports", "state"):
        paths[name] = tmp_path / name
        paths[name].mkdir()
    runtime = RuntimeConfig(paths=PathConfig(**paths))
    get_state().register_db(db)
    get_state().register_runtime(runtime)
    return db, runtime, tmp_path


def _tariff(db, *, local: str, fee: str, feed: str | None = None, valid_from=DAY, valid_until=None, name="Test"):
    feed_in = Decimal(feed) if feed is not None else Decimal(local) - Decimal(fee)
    create_tariff(db, Tariff(local_rate_chf_kwh=Decimal(local), grid_rate_chf_kwh=Decimal("0"),
                             feed_in_rate_chf_kwh=feed_in, admin_fee_chf_kwh=Decimal(fee), name=name,
                             valid_from=valid_from, valid_until=valid_until))


def _flow(point: str, direction: str, total: list[str], local: list[str]) -> list[Series]:
    residual = [f"{Decimal(t) - Decimal(l):.3f}" for t, l in zip(total, local)]
    return [Series(point, direction, "total", total, start=DAY_START),
            Series(point, direction, "local", local, start=DAY_START),
            Series(point, direction, "residual", residual, start=DAY_START)]


def _deliver(db, tmp, flows: dict[tuple[str, str], tuple[list[str], list[str]]], name: str = "e66.xml"):
    """One E66 monthly delivery with `flows` ((mpid, direction) -> (total, local) per slot) and its E31."""
    series = [s for (mpid, direction), (total, local) in flows.items() for s in _flow(mpid, direction, total, local)]
    sums: dict[tuple[str, str], list[Decimal]] = {}
    for s in series:
        values = [Decimal(v) for v in s.values]
        key = (s.direction, s.energy_type)
        sums[key] = [a + b for a, b in zip(sums[key], values)] if key in sums else values
    totals = [Series(GRID_AREA, d, e, [f"{v:.3f}" for v in vals], start=DAY_START) for (d, e), vals in sorted(sums.items())]
    for doc_type, doc_series, file_name in (("E66", series, name), ("E31", totals, "e31-" + name)):
        data = build(Document(document_type=doc_type, series=doc_series, report_start=DAY_START, report_end=MONTH_END,
                              document_id=f"B28-{file_name}"), DAY_START)
        path = tmp / file_name
        path.write_bytes(data)
        outcome = import_sdat_file(db, path, hashlib.sha256(data).hexdigest(), file_name=file_name, origin="swisseldex")
        assert outcome.imported, outcome


def _standard_flows(slots: int = SLOTS) -> dict:
    """Two consumers, a prosumer and two producers; LEG energy drawn = LEG energy supplied every quarter hour."""
    return {
        (C1, "consumption"): (["2.000"] * slots, ["1.000"] * slots),
        (C2, "consumption"): (["1.000"] * slots, ["0.500"] * slots),
        (PROSUMER, "consumption"): (["0.500"] * slots, ["0.250"] * slots),
        (PROSUMER, "production"): (["0.400"] * slots, ["0.250"] * slots),
        (P1, "production"): (["3.000"] * slots, ["1.000"] * slots),
        (P2, "production"): (["1.000"] * slots, ["0.500"] * slots),
    }


def _released(db, runtime, last_day: date = DAY) -> int:
    preview = billing.compute_billing_preview(db, runtime, DAY, last_day)
    assert preview.availability.blocking_issues == [], preview.availability.blocking_issues
    run = billing.save_draft(db, preview)
    billing.release_billing_run(db, run.id, runtime=runtime)
    return run.id


def _documents(db, run_id):
    docs = invoicing.issue_documents(db, run_id, render_document, today=ISSUED)
    invoices = sum((d.amount_chf for d in docs if d.doc_type == "invoice"), Decimal(0))
    credits = sum((d.amount_chf for d in docs if d.doc_type == "credit"), Decimal(0))
    return docs, invoices, credits


# ── the balance holds ──


def test_without_admin_fee_invoices_equal_credits(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0")
    _deliver(db, tmp, _standard_flows())
    run_id = _released(db, runtime)
    money = billing.get_billing_run(db, run_id).run.money
    assert money.admin_fee == 0 and money.rounding == 0 and money.consumers == money.producers
    docs, invoices, credits = _documents(db, run_id)
    assert invoices == credits > 0
    assert {d.participant_id for d in docs} == {"c1", "c2", "p1", "p2"}     # the prosumer nets to zero


def test_with_admin_fee_every_document_is_issued_and_the_fee_is_the_difference(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.02")
    _deliver(db, tmp, _standard_flows())
    run_id = _released(db, runtime)
    money = billing.get_billing_run(db, run_id).run.money
    received = Decimal("1.750") * SLOTS                       # c1 + c2 + prosumer per quarter hour
    assert money.admin_fee == received * Decimal("0.02")
    assert money.consumers - money.producers == money.admin_fee and money.rounding == 0
    docs, invoices, credits = _documents(db, run_id)
    assert {d.participant_id for d in docs} == {"c1", "c2", "pro", "p1", "p2"}
    assert invoices == _cent(money.consumers - Decimal("0.25") * SLOTS * Decimal("0.18")) or invoices > credits
    assert invoices - credits == _cent(money.consumers) - _cent(money.producers)
    assert invoicing.issue_problems(db, run_id, today=ISSUED) == []


def test_prosumer_is_netted_and_keeps_its_fee_share(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.02")
    _deliver(db, tmp, _standard_flows())
    run_id = _released(db, runtime)
    lines = {i.participant_id: i for i in billing.get_billing_run(db, run_id).line_items}
    pro = lines["pro"]
    # draws 0.25 kWh at 0.20 and supplies 0.25 kWh at 0.18 per quarter hour: pays the fee on its own draw
    assert pro.charge_chf == _cent(Decimal("0.25") * SLOTS * Decimal("0.20"))
    assert pro.credit_chf == _cent(Decimal("0.25") * SLOTS * Decimal("0.18"))
    assert pro.net_amount_chf == pro.charge_chf - pro.credit_chf > 0
    docs, _, _ = _documents(db, run_id)
    assert [d.amount_chf for d in docs if d.participant_id == "pro"] == [pro.net_amount_chf]


def test_two_price_windows_with_different_fees(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.01", valid_from=DAY, valid_until=DAY, name="Fenster 1")
    _tariff(db, local="0.24", fee="0.03", valid_from=DAY + timedelta(days=1), name="Fenster 2")
    _deliver(db, tmp, _standard_flows(2 * SLOTS))
    run_id = _released(db, runtime, last_day=DAY + timedelta(days=1))
    money = billing.get_billing_run(db, run_id).run.money
    assert money.admin_fee == Decimal("1.750") * SLOTS * Decimal("0.01") + Decimal("1.750") * SLOTS * Decimal("0.03")
    assert money.consumers - money.producers == money.admin_fee
    docs, invoices, credits = _documents(db, run_id)
    assert invoices - credits == _cent(money.consumers) - _cent(money.producers)


def test_cent_rounding_is_one_rounding_of_the_exact_sums(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.2037", fee="0.0111")                 # amounts with fractions of a cent
    flows = {
        (C1, "consumption"): (["0.400"] * SLOTS, ["0.137"] * SLOTS),
        (C2, "consumption"): (["0.400"] * SLOTS, ["0.137"] * SLOTS),
        (PROSUMER, "consumption"): (["0.400"] * SLOTS, ["0.137"] * SLOTS),
        (PROSUMER, "production"): (["0.100"] * SLOTS, ["0.000"] * SLOTS),
        (P1, "production"): (["0.500"] * SLOTS, ["0.411"] * SLOTS),
        (P2, "production"): (["0.200"] * SLOTS, ["0.000"] * SLOTS),
    }
    _deliver(db, tmp, flows)
    run_id = _released(db, runtime)
    detail = billing.get_billing_run(db, run_id)
    money = detail.run.money
    assert money.consumers != _cent(money.consumers)          # really a fraction of a cent
    assert sum(i.charge_chf for i in detail.line_items) == _cent(money.consumers)
    assert sum(i.credit_chf for i in detail.line_items) == _cent(money.producers)
    _documents(db, run_id)


def test_grid_operator_rounding_is_part_of_the_balance_not_suppressed(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.02")
    flows = _standard_flows()
    total, local = flows[(C1, "consumption")]
    local = list(local)
    local[5] = "1.001"                                        # EBL rounds the split: +0.001 kWh drawn
    flows[(C1, "consumption")] = (total, local)
    _deliver(db, tmp, flows)
    run_id = _released(db, runtime)
    money = billing.get_billing_run(db, run_id).run.money
    assert money.rounding == Decimal("0.001") * Decimal("0.18")
    assert money.consumers - money.producers == money.admin_fee + money.rounding
    _documents(db, run_id)


# ── the balance still refuses real deviations ──


def _tampered(db, sql: str, params: tuple):
    with connect(db) as conn:
        with conn:
            conn.execute(sql, params)


def test_a_changed_invoice_amount_is_refused(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.02")
    _deliver(db, tmp, _standard_flows())
    run_id = _released(db, runtime)
    _tampered(db, "UPDATE billing_records SET charge_chf = CAST(charge_chf + 0.01 AS TEXT),"
                  " net_amount_chf = CAST(net_amount_chf + 0.01 AS TEXT) WHERE billing_run_id = ? AND participant_id = 'c1'",
              (run_id,))
    problems = invoicing.issue_problems(db, run_id, today=ISSUED)
    assert any(p.startswith("Rappenabstimmung fehlgeschlagen: Rechnungen") for p in problems)
    with pytest.raises(invoicing.InvoicingError):
        invoicing.issue_documents(db, run_id, render_document, today=ISSUED)


def test_a_balance_that_does_not_match_its_lines_is_refused(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.02")
    _deliver(db, tmp, _standard_flows())
    run_id = _released(db, runtime)
    _tampered(db, "UPDATE billing_records SET net_amount_chf = '1.00' WHERE billing_run_id = ? AND participant_id = 'c2'",
              (run_id,))
    assert any("Saldo CHF 1.00" in p for p in invoicing.issue_problems(db, run_id, today=ISSUED))


def test_a_changed_fee_is_refused(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.02")
    _deliver(db, tmp, _standard_flows())
    run_id = _released(db, runtime)
    _tampered(db, "UPDATE billing_runs SET money_admin_fee_chf = '0' WHERE id = ?", (run_id,))
    assert any(p.startswith("Geldabstimmung fehlgeschlagen") for p in invoicing.issue_problems(db, run_id, today=ISSUED))


def test_a_margin_without_contractual_basis_is_refused(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.01", feed="0.15")          # 0.04 per kWh nobody agreed on
    _deliver(db, tmp, _standard_flows())
    run_id = _released(db, runtime)
    problems = invoicing.issue_problems(db, run_id, today=ISSUED)
    assert any("Differenz ohne vertragliche Grundlage" in p for p in problems)


def test_run_saved_before_0631_keeps_the_old_rule(leg):
    db, runtime, tmp = leg
    _tariff(db, local="0.20", fee="0.02")
    _deliver(db, tmp, _standard_flows())
    run_id = _released(db, runtime)
    _tampered(db, "UPDATE billing_runs SET money_consumers_chf = '', money_producers_chf = '', money_admin_fee_chf = '',"
                  " money_rounding_chf = '' WHERE id = ?", (run_id,))
    assert billing.get_billing_run(db, run_id).run.money is None
    problems = invoicing.issue_problems(db, run_id, today=ISSUED)
    assert any("vor Version 0.6.31 gespeichert" in p for p in problems)
