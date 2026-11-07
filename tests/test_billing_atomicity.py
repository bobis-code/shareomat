# -*- coding: utf-8 -*-
"""
File: tests/test_billing_atomicity.py

Purpose:
    Deterministic, separate-connection regressions for billing transitions.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database import billing, invoicing
from shareomat.database.audit import list_events
from shareomat.database.community import save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import connect, init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.invoicing import PaymentProfile
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff
from shareomat.web.pages import billing as billing_page, invoices as invoices_page
from shareomat.web.pages.invoices import render_document
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state

DAY = date(2027, 7, 1)


@pytest.fixture
def case(tmp_path):
    db = tmp_path / "billing.db"
    init_db(db)
    save_community(db, Community(community_id="AUDIT-TEST", name="Synthetische Test-LEG",
                                 address_line="Beispielweg 0", postal_code="0000", city="Testort"))
    for pid, role in (("test-pv", "producer"), ("test-load", "consumer")):
        create_participant(db, Participant(pid, pid, address_line="Beispielweg 0",
                                          postal_code="0000", city="Testort"))
        create_meter(db, Meter(pid, pid, pid, role))
    create_tariff(db, Tariff(local_rate_chf_kwh=Decimal("0.10"), grid_rate_chf_kwh=Decimal("0"),
                             feed_in_rate_chf_kwh=Decimal("0.10"), valid_from=date(2020, 1, 1)))
    paths = {}
    for name in ("inbox", "archive", "reports", "state"):
        paths[name] = tmp_path / name
        paths[name].mkdir()
    runtime = RuntimeConfig(paths=PathConfig(**paths))
    source = paths["inbox"] / "test-day.csv"
    with source.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        start = datetime(2027, 6, 30, 22, tzinfo=timezone.utc)
        for slot in range(96):
            timestamp = (start + timedelta(minutes=15 * slot)).isoformat()
            writer.writerow([timestamp, "test-pv", "1", "export"])
            writer.writerow([timestamp, "test-load", "1", "import"])
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    assert not preview.availability.blocking_issues
    run = billing.save_draft(db, preview)
    get_state().register_db(db)
    get_state().register_runtime(runtime)
    return db, runtime, run.id, source


def _action(db, run_id, action):
    if action == "release":
        return billing.release_billing_run(db, run_id)
    return billing.cancel_billing_run(db, run_id)


def _snapshot(db, table):
    assert table in {"billing_records", "billing_sources", "billing_documents"}
    with connect(db) as conn:
        return [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id")]


def _pause_update(monkeypatch, module, table):
    """Pause only the named worker just before its SQL mutation, never by sleeping."""
    reached, resume = threading.Event(), threading.Event()
    real_connect = module.connect
    connections = []

    class Proxy:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def execute(self, sql, parameters=()):
            if threading.current_thread().name.startswith("paused") and sql.startswith(f"UPDATE {table} SET status"):
                reached.set()
                assert resume.wait(15), "The test did not resume the paused transaction"
            return self.conn.execute(sql, parameters)

    @contextmanager
    def wrapped(path):
        with real_connect(path) as conn:
            connections.append(conn)
            conn.execute("PRAGMA busy_timeout = 0")
            yield Proxy(conn)

    monkeypatch.setattr(module, "connect", wrapped)
    return reached, resume, connections


@pytest.mark.parametrize("first,second", [("release", "cancel"), ("release", "release"),
                                        ("cancel", "release"), ("cancel", "cancel")])
def test_competing_transitions_never_resurrect_or_release_twice(case, monkeypatch, first, second):
    db, _, run_id, _ = case
    records = _snapshot(db, "billing_records")
    reached, resume, connections = _pause_update(monkeypatch, billing, "billing_runs")
    retry = False
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="paused") as pool:
        future = pool.submit(_action, db, run_id, first)
        try:
            assert reached.wait(15)
            try:
                _action(db, run_id, second)
            except billing.BillingWorkflowError as exc:
                assert "gesperrt" in str(exc)
                retry = True
        finally:
            resume.set()
        future.result(timeout=15)
    assert len({id(conn) for conn in connections}) >= 2
    if retry:
        if first == second or first == "cancel":
            with pytest.raises(billing.BillingWorkflowError):
                _action(db, run_id, second)
        else:
            _action(db, run_id, second)
    expected = "cancelled" if "cancel" in (first, second) else "released"
    assert billing.get_billing_run(db, run_id).run.status == expected
    events = list_events(db, entity="billing_run", entity_id=run_id)
    assert sum(event.action == "released" for event in events) <= 1
    assert _snapshot(db, "billing_records") == records


@pytest.mark.parametrize("change", ["tariff", "membership", "file", "new_file"])
def test_basis_change_after_recomputation_refuses_release_and_can_retry(case, monkeypatch, change):
    db, runtime, run_id, source = case
    original = billing.compute_billing_preview
    changed = threading.Event()
    resume = threading.Event()

    def paused(*args, **kwargs):
        preview = original(*args, **kwargs)
        changed.set()
        assert resume.wait(15)
        return preview

    monkeypatch.setattr(billing, "compute_billing_preview", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(billing.release_billing_run, db, run_id, runtime=runtime)
        try:
            assert changed.wait(15)
            if change in ("tariff", "membership"):
                with connect(db) as conn:
                    with conn:
                        if change == "tariff":
                            conn.execute("UPDATE tariffs SET local_rate_chf_kwh = '0.20'")
                        else:
                            conn.execute("UPDATE participants SET active = 0 WHERE participant_id = 'test-load'")
            elif change == "file":
                source.write_text(source.read_text(encoding="utf-8").replace(",1,import", ",2,import"), encoding="utf-8")
            else:
                (runtime.paths.inbox / "test-additional.csv").write_bytes(source.read_bytes())
        finally:
            resume.set()
        with pytest.raises(billing.BillingWorkflowError, match="geändert"):
            future.result(timeout=15)
    assert billing.get_billing_run(db, run_id).run.status == "draft"
    assert not list_events(db, entity="billing_run", entity_id=run_id)
    monkeypatch.setattr(billing, "compute_billing_preview", original)
    new_preview = original(db, runtime, DAY, DAY)
    if change != "membership":
        new_run = billing.save_draft(db, new_preview)
        billing.release_billing_run(db, new_run.id, runtime=runtime)


@pytest.mark.parametrize("action", ["release", "cancel", "issue", "cancel_document"])
def test_lock_errors_are_domain_errors_and_retry_is_safe(case, monkeypatch, action):
    db, runtime, run_id, _ = case
    if action in {"issue", "cancel_document"}:
        billing.release_billing_run(db, run_id, runtime=runtime)
        _ready_profile(db)
    if action == "cancel_document":
        doc_id = invoicing.issue_documents(db, run_id, render_document, today=DAY)[0].id
    module = invoicing if action in {"issue", "cancel_document"} else billing
    real_connect = module.connect

    @contextmanager
    def no_wait(path):
        with real_connect(path) as conn:
            conn.execute("PRAGMA busy_timeout = 0")
            yield conn

    monkeypatch.setattr(module, "connect", no_wait)

    def request():
        if action == "issue":
            return invoicing.issue_documents(db, run_id, render_document, today=DAY)
        if action == "cancel_document":
            return invoicing.cancel_document(db, doc_id, "Synthetischer Test", render_document, today=DAY)
        return _action(db, run_id, action)

    before = _snapshot(db, "billing_documents")
    with connect(db) as holder:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(module.InvoicingError if module is invoicing else billing.BillingWorkflowError,
                           match="gesperrt"):
            request()
        assert _snapshot(db, "billing_documents") == before
        holder.rollback()
    request()


def _ready_profile(db):
    invoicing.save_payment_profile(db, PaymentProfile(payee="Synthetische Test-LEG", street="Beispielweg",
                                                     building="0", postal_code="0000", city="Testort",
                                                     iban="CH93 0076 2011 6238 5295 7"))
    invoicing.confirm_payment_profile(db, version=invoicing.get_payment_profile(db).version)


def test_audit_failure_rolls_back_release_and_retry(case, monkeypatch):
    db, runtime, run_id, _ = case
    original = billing.record_event

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected audit failure")

    monkeypatch.setattr(billing, "record_event", fail)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        billing.release_billing_run(db, run_id, runtime=runtime)
    assert billing.get_billing_run(db, run_id).run.status == "draft"
    assert not list_events(db, entity="billing_run", entity_id=run_id)
    monkeypatch.setattr(billing, "record_event", original)
    billing.release_billing_run(db, run_id, runtime=runtime)


def test_sequential_release_issue_cancel_preserves_snapshots(case):
    db, runtime, run_id, _ = case
    records = _snapshot(db, "billing_records")
    sources = _snapshot(db, "billing_sources")
    billing.release_billing_run(db, run_id, runtime=runtime)
    _ready_profile(db)
    docs = invoicing.issue_documents(db, run_id, render_document, today=DAY)
    assert len(docs) == 2
    with pytest.raises(billing.BillingWorkflowError, match="zuerst die Belege"):
        billing.cancel_billing_run(db, run_id)
    for doc in docs:
        invoicing.cancel_document(db, doc.id, "Synthetischer Test", render_document, today=DAY)
        saved = invoicing.get_document(db, doc.id)
        assert saved.snapshot_html == doc.snapshot_html
        assert saved.snapshot_sha256 == doc.snapshot_sha256
        assert saved.integrity_ok
    billing.cancel_billing_run(db, run_id)
    with pytest.raises(billing.BillingWorkflowError):
        billing.release_billing_run(db, run_id, runtime=runtime)
    assert _snapshot(db, "billing_records") == records
    assert _snapshot(db, "billing_sources") == sources


def test_release_check_does_not_write_meter_readings(case, monkeypatch):
    db, runtime, run_id, _ = case

    def forbidden(*args, **kwargs):
        pytest.fail("Release validation must not write through a second connection")

    monkeypatch.setattr(billing, "save_meter_readings", forbidden)
    billing.release_billing_run(db, run_id, runtime=runtime)


def test_waiting_release_rechecks_after_first_writer_commits(case, monkeypatch):
    db, _, run_id, _ = case
    real_connect = billing.connect
    first_ready, second_attempted, resume = (threading.Event() for _ in range(3))

    class Proxy:
        def __init__(self, conn):
            self.conn = conn

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def __enter__(self):
            self.conn.__enter__()
            return self

        def __exit__(self, *args):
            return self.conn.__exit__(*args)

        def execute(self, sql, parameters=()):
            name = threading.current_thread().name
            if name.startswith("second") and sql == "BEGIN IMMEDIATE":
                second_attempted.set()
            if name.startswith("first") and sql.startswith("UPDATE billing_runs SET status"):
                first_ready.set()
                assert resume.wait(15)
            return self.conn.execute(sql, parameters)

    @contextmanager
    def wrapped(path):
        with real_connect(path) as conn:
            yield Proxy(conn)

    monkeypatch.setattr(billing, "connect", wrapped)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="first") as first_pool:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="second") as second_pool:
            first = first_pool.submit(billing.release_billing_run, db, run_id)
            try:
                assert first_ready.wait(15)
                second = second_pool.submit(billing.release_billing_run, db, run_id)
                assert second_attempted.wait(15)
            finally:
                resume.set()
            first.result(timeout=15)
            with pytest.raises(billing.BillingWorkflowError, match="Nur Entwürfe"):
                second.result(timeout=15)
    assert len(list_events(db, entity="billing_run", entity_id=run_id)) == 1


def _no_wait_connections(monkeypatch):
    @contextmanager
    def no_wait(path):
        with connect(path) as conn:
            conn.execute("PRAGMA busy_timeout = 0")
            yield conn

    monkeypatch.setattr(billing, "connect", no_wait)
    monkeypatch.setattr(invoicing, "connect", no_wait)


@pytest.mark.parametrize("competitor", ["issue", "cancel_run"])
def test_issuing_serializes_with_second_issuer_and_run_cancellation(case, monkeypatch, competitor):
    db, runtime, run_id, _ = case
    billing.release_billing_run(db, run_id, runtime=runtime)
    _ready_profile(db)
    _no_wait_connections(monkeypatch)
    rendering, resume = threading.Event(), threading.Event()

    def paused_render(*args, **kwargs):
        rendering.set()
        assert resume.wait(15)
        return render_document(*args, **kwargs)

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(invoicing.issue_documents, db, run_id, paused_render, today=DAY)
        try:
            assert rendering.wait(15)
            if competitor == "issue":
                with pytest.raises(invoicing.InvoicingError, match="gesperrt"):
                    invoicing.issue_documents(db, run_id, render_document, today=DAY)
            else:
                with pytest.raises(billing.BillingWorkflowError, match="gesperrt"):
                    billing.cancel_billing_run(db, run_id)
        finally:
            resume.set()
        docs = first.result(timeout=15)
    snapshots = _snapshot(db, "billing_documents")
    if competitor == "issue":
        assert invoicing.issue_documents(db, run_id, render_document, today=DAY) == []
    else:
        with pytest.raises(billing.BillingWorkflowError, match="zuerst die Belege"):
            billing.cancel_billing_run(db, run_id)
    assert len(docs) == 2
    assert len({doc.number for doc in docs}) == 2
    assert len({doc.participant_id for doc in docs}) == 2
    assert all(doc.integrity_ok for doc in docs)
    assert _snapshot(db, "billing_documents") == snapshots


def test_cancellation_commits_before_issuer_can_write(case, monkeypatch):
    db, runtime, run_id, _ = case
    billing.release_billing_run(db, run_id, runtime=runtime)
    _ready_profile(db)
    _no_wait_connections(monkeypatch)
    reached, resume, _ = _pause_update(monkeypatch, billing, "billing_runs")
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="paused") as pool:
        cancelling = pool.submit(billing.cancel_billing_run, db, run_id)
        try:
            assert reached.wait(15)
            with pytest.raises(invoicing.InvoicingError, match="gesperrt"):
                invoicing.issue_documents(db, run_id, render_document, today=DAY)
        finally:
            resume.set()
        cancelling.result(timeout=15)
    with pytest.raises(invoicing.InvoicingError, match="Nur freigegebene"):
        invoicing.issue_documents(db, run_id, render_document, today=DAY)
    assert _snapshot(db, "billing_documents") == []


@pytest.mark.parametrize("action", ["release", "cancel", "issue"])
def test_web_endpoints_present_lock_conflict_as_form_error(case, monkeypatch, action):
    db, _, run_id, _ = case
    _no_wait_connections(monkeypatch)
    if action == "issue":
        page = invoices_page
        ctx = RequestContext(method="POST", ingress_path="", segments=["issue"],
                             form={"run_id": str(run_id), "confirm": "1", "fingerprint": "test"})
    else:
        page = billing_page
        ctx = RequestContext(method="POST", ingress_path="", segments=[str(run_id), action])
    with connect(db) as holder:
        holder.execute("BEGIN IMMEDIATE")
        with pytest.raises(FormError, match="gesperrt"):
            page.handle_post(ctx)
        holder.rollback()


def test_second_document_failure_rolls_back_whole_issue_and_numbers(case, monkeypatch):
    db, runtime, run_id, _ = case
    billing.release_billing_run(db, run_id, runtime=runtime)
    _ready_profile(db)
    original = invoicing.record_event
    count = 0

    def fail_second(conn, entity, entity_id, action, detail):
        nonlocal count
        if entity == "billing_document" and action == "issued":
            count += 1
            if count == 2:
                raise sqlite3.OperationalError("injected second-document failure")
        return original(conn, entity, entity_id, action, detail)

    monkeypatch.setattr(invoicing, "record_event", fail_second)
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        invoicing.issue_documents(db, run_id, render_document, today=DAY)
    assert _snapshot(db, "billing_documents") == []
    assert not list_events(db, entity="billing_document")
    monkeypatch.setattr(invoicing, "record_event", original)
    docs = invoicing.issue_documents(db, run_id, render_document, today=DAY)
    assert sorted(doc.number for doc in docs) == ["G-2027-001", "R-2027-001"]


def test_legacy_fingerprint_and_optional_runtime_remain_compatible(case):
    db, runtime, run_id, _ = case
    with connect(db) as conn:
        with conn:
            conn.execute("UPDATE billing_runs SET result_fingerprint = '' WHERE id = ?", (run_id,))
    assert billing.release_billing_run(db, run_id, runtime=runtime).status == "released"
    billing.cancel_billing_run(db, run_id)
    assert billing.get_billing_run(db, run_id).run.status == "cancelled"


@pytest.mark.parametrize("action", ["release", "cancel"])
def test_conditional_update_must_affect_exactly_one_run(case, action):
    db, _, run_id, _ = case
    with connect(db) as conn:
        with conn:
            conn.execute("CREATE TRIGGER test_ignore_transition BEFORE UPDATE OF status ON billing_runs "
                         "BEGIN SELECT RAISE(IGNORE); END")
    with pytest.raises(billing.BillingWorkflowError, match="zwischenzeitlich geändert"):
        _action(db, run_id, action)
    assert billing.get_billing_run(db, run_id).run.status == "draft"
    assert not list_events(db, entity="billing_run", entity_id=run_id)
    with connect(db) as conn:
        with conn:
            conn.execute("DROP TRIGGER test_ignore_transition")
    _action(db, run_id, action)


def test_changed_then_restored_database_basis_still_requires_new_validation(case, monkeypatch):
    db, runtime, run_id, _ = case
    original = billing.compute_billing_preview
    computed, resume = threading.Event(), threading.Event()

    def paused(*args, **kwargs):
        result = original(*args, **kwargs)
        computed.set()
        assert resume.wait(15)
        return result

    monkeypatch.setattr(billing, "compute_billing_preview", paused)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(billing.release_billing_run, db, run_id, runtime=runtime)
        try:
            assert computed.wait(15)
            with connect(db) as conn:
                with conn:
                    conn.execute("UPDATE tariffs SET local_rate_chf_kwh = '0.20'")
                with conn:
                    conn.execute("UPDATE tariffs SET local_rate_chf_kwh = '0.10'")
        finally:
            resume.set()
        with pytest.raises(billing.BillingWorkflowError, match="Datenbank.*geändert"):
            future.result(timeout=15)
    assert billing.get_billing_run(db, run_id).run.status == "draft"


def test_missing_configuration_is_controlled_and_does_not_release(case):
    db, runtime, run_id, _ = case
    with connect(db) as conn:
        with conn:
            conn.execute("UPDATE participants SET active = 0")
    with pytest.raises(billing.BillingWorkflowError, match="nicht mehr vollständig"):
        billing.release_billing_run(db, run_id, runtime=runtime)
    assert billing.get_billing_run(db, run_id).run.status == "draft"
