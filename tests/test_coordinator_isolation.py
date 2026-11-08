# -*- coding: utf-8 -*-
"""coordinator.db and the orderly coordinator stop.

Real SQLite files and the real billing release. Stubbed are only the network
downloads, the pauses that fix the timing, and the MQTT client.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import main as app
from shareomat.config import MqttConfig, SparkplugConfig
from shareomat.core.pipeline import export_price_forecast as prices
from shareomat.database import billing
from shareomat.database.consumption_forecasts import latest_computed_at, list_consumption_forecasts, save_consumption_forecast
from shareomat.database.day_ahead_prices import list_day_ahead_prices
from shareomat.database.external_settings import save_external_data_settings
from shareomat.database.sqlite import init_db
from shareomat.models.external_data import ExternalDataSettings
from shareomat.models.forecast import ConsumptionForecastPoint
from shareomat.sparkplug.coordinator_store import coordinator_db_path, init_coordinator_db
from shareomat.sparkplug.host import SparkplugHost
from tests.test_billing_atomicity import case  # noqa: F401
from tests.test_config import _leg_config

SOON = datetime.now(timezone.utc) + timedelta(hours=2)


class Client:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append(topic)

    def loop_stop(self):
        pass

    def disconnect(self):
        pass

    def ncmd(self):
        return [t for t in self.published if "/NCMD/" in t]


def _host():
    client = Client()
    host = SparkplugHost(SparkplugConfig(enabled=True, emsomat_edge_node_id="n"),
                         MqttConfig(enabled=True, broker="b", port=1883, client_id="c"), client=client)
    host._connected.set()
    host._emsomat_online = True
    return host, client


def _with_prices(db, monkeypatch):
    save_external_data_settings(db, ExternalDataSettings(entsoe_api_token="t"))
    monkeypatch.setattr(prices, "download_day_ahead_prices", lambda *a, **k: [
        SimpleNamespace(start=SOON + timedelta(hours=i), price_eur_mwh=Decimal("100")) for i in range(24)
    ])
    monkeypatch.setattr(prices, "download_exchange_rates", lambda *a, **k: [])


def _forecast(n=672):
    return [ConsumptionForecastPoint(scope="leg", participant_id="", slot_start=SOON + timedelta(minutes=15 * i), forecast_kwh=1.0,
                                     quality="ok", method="m", sample_count=4, data_period_start=None, data_period_end=None)
            for i in range(n)]


def _data_version(connection):
    return connection.execute("PRAGMA data_version").fetchone()[0]


def _release_during(case, monkeypatch, during):
    """A real release, paused between its recomputation and its data_version check."""
    db, runtime, run_id, _ = case
    original = billing.compute_billing_preview
    paused, resume = threading.Event(), threading.Event()

    def pausing(*args, **kwargs):
        preview = original(*args, **kwargs)
        paused.set()
        assert resume.wait(30)
        return preview

    monkeypatch.setattr(billing, "compute_billing_preview", pausing)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(billing.release_billing_run, db, run_id, runtime=runtime)
        assert paused.wait(30)
        try:
            during(db)
        finally:
            resume.set()
        return future.result(timeout=30)


@pytest.fixture(autouse=True)
def clean_coordinator(monkeypatch):
    monkeypatch.setattr(app, "_COORDINATOR_ONLINE_WAIT_SECONDS", 0.0)
    app._COORDINATOR_STOP.clear()
    yield
    if app._coordinator_thread is not None:
        app._coordinator_thread.join(timeout=15)
    app._coordinator_thread = None
    app._COORDINATOR_STOP.clear()


# ── coordinator.db ───────────────────────────────────────────────────────────


def test_store_lies_next_to_shareomat_db_and_has_only_its_two_tables(tmp_path):
    db = tmp_path / "data" / "shareomat.db"
    store = coordinator_db_path(db)
    assert store == tmp_path / "data" / "coordinator.db"
    init_coordinator_db(store)
    con = sqlite3.connect(store)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    finally:
        con.close()
    assert tables == {"consumption_forecasts", "day_ahead_prices"}


def test_coordinator_writes_do_not_change_data_version(case, monkeypatch):
    db, *_ = case
    _with_prices(db, monkeypatch)
    host, client = _host()
    watcher = sqlite3.connect(db)
    try:
        before = _data_version(watcher)
        app._run_coordinator_tasks(host, _leg_config(), db)
        assert _data_version(watcher) == before

        store = coordinator_db_path(db)
        assert list_consumption_forecasts(store, scope="leg") is not None and latest_computed_at(store, scope="leg") is not None
        assert len(list_day_ahead_prices(store)) == 24
        assert client.ncmd(), "the LEG data still reach Emsomat automatically"

        other = sqlite3.connect(db)      # the probe does see a real commit
        with other:
            other.execute("CREATE TABLE IF NOT EXISTS probe (x)")
        other.close()
        assert _data_version(watcher) != before
    finally:
        watcher.close()


def test_release_succeeds_while_the_coordinator_runs(case, monkeypatch):
    db, *_ = case
    _with_prices(db, monkeypatch)
    host, client = _host()
    run = _release_during(case, monkeypatch, lambda d: app._run_coordinator_tasks(host, _leg_config(), d))
    assert run.status == "released" and client.ncmd()


def test_release_succeeds_during_continuous_coordinator_writes(case, monkeypatch):
    stop = threading.Event()
    writes = []

    def writer(d):
        store = app._coordinator_store(d)
        while not stop.is_set():
            save_consumption_forecast(store, _forecast())
            writes.append(1)

    def during(d):
        thread = threading.Thread(target=writer, args=(d,))
        thread.start()
        time.sleep(0.5)
        stop.set()
        thread.join(timeout=10)

    assert _release_during(case, monkeypatch, during).status == "released"
    assert len(writes) >= 2


def test_writer_holding_the_coordinator_db_does_not_touch_billing_writes(case, monkeypatch):
    db, *_ = case
    store = app._coordinator_store(db)
    holder = sqlite3.connect(store)
    holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.perf_counter()
        assert _release_during(case, monkeypatch, lambda d: None).status == "released"
        assert time.perf_counter() - started < 5, "billing never waits for the coordinator database"
    finally:
        holder.rollback()
        holder.close()


@pytest.mark.parametrize("damage", ["garbage", "directory"])
def test_broken_coordinator_db_does_not_block_billing(case, monkeypatch, caplog, damage):
    db, *_ = case
    store = coordinator_db_path(db)
    if damage == "garbage":
        store.write_bytes(b"keine datenbank " * 500)
    else:
        store.mkdir()
    host, client = _host()
    with caplog.at_level(logging.ERROR, logger="main"):
        run = _release_during(case, monkeypatch, lambda d: app._run_coordinator_tasks(host, _leg_config(), d))
    assert run.status == "released"
    assert "coordinator step failed" in caplog.text and client.ncmd() == []


def test_settlement_cycle_runs_with_a_broken_coordinator_db(tmp_path, monkeypatch, caplog):
    db = tmp_path / "shareomat.db"
    init_db(db)
    coordinator_db_path(db).write_bytes(b"keine datenbank " * 500)
    runs = []
    monkeypatch.setattr(app, "build_leg_config", lambda d, r: _leg_config())
    monkeypatch.setattr(app, "run", lambda c, mqtt_client=None, db_path=None: runs.append(c))
    monkeypatch.setattr(app, "_update_web_state", lambda *a, **k: None)
    runtime = SimpleNamespace(paths=SimpleNamespace())
    host, _ = _host()
    with caplog.at_level(logging.ERROR, logger="main"):
        assert app._run_safe_cycle(runtime, db, None, host) is True
        app._coordinator_thread.join(timeout=10)
    assert len(runs) == 1 and "coordinator step failed" in caplog.text


def test_store_is_reused_after_a_restart(tmp_path, monkeypatch):
    db = tmp_path / "shareomat.db"
    init_db(db)
    host, _ = _host()
    app._run_coordinator_tasks(host, _leg_config(), db)
    store = coordinator_db_path(db)
    first = latest_computed_at(store, scope="leg")
    assert first is not None
    save_consumption_forecast(store, _forecast(4))

    init_coordinator_db(store)      # what a new start does
    app._run_coordinator_tasks(host, _leg_config(), db)
    assert latest_computed_at(store, scope="leg") >= first
    assert len(list_consumption_forecasts(store, scope="leg", start=SOON - timedelta(minutes=1))) >= 4


def test_old_rows_stay_and_the_forecast_is_rebuilt_automatically(tmp_path):
    db = tmp_path / "shareomat.db"
    init_db(db)
    save_consumption_forecast(db, _forecast(8))
    host, client = _host()
    app._run_coordinator_tasks(host, _leg_config(), db)
    assert len(list_consumption_forecasts(db, scope="leg")) == 8
    assert latest_computed_at(coordinator_db_path(db), scope="leg") is not None and client.ncmd()


# ── Orderly stop ─────────────────────────────────────────────────────────────


def _blocked(monkeypatch, step):
    """Block one step until released, and record what happens afterwards."""
    started, release = threading.Event(), threading.Event()
    events = []
    names = ["_maybe_recompute_demand_forecast", "_maybe_recompute_export_price_forecast"]
    for name in names:
        monkeypatch.setattr(app, name, lambda d: None)

    def blocking(d):
        started.set()
        release.wait(20)
        events.append("step finished")

    monkeypatch.setattr(app, step, blocking)
    monkeypatch.setattr(app, "_publish_coordinator_sparkplug", lambda *a: events.append("published"))
    return started, release, events


def _start(host, tmp_path):
    app._start_coordinator_tasks(host, object(), tmp_path / "x.db")


def test_stop_waits_for_a_running_forecast_before_the_clients_go(tmp_path, monkeypatch):
    started, release, events = _blocked(monkeypatch, "_maybe_recompute_demand_forecast")
    host = SimpleNamespace(emsomat_online=True, stop=lambda: events.append("client stopped"))
    _start(host, tmp_path)
    assert started.wait(5)
    threading.Timer(0.3, release.set).start()
    app._stop_sparkplug((host,))
    assert events == ["step finished", "client stopped"]
    assert not app._coordinator_thread.is_alive()


def test_hanging_price_request_is_left_behind_and_sends_nothing(tmp_path, monkeypatch, caplog):
    started, release, events = _blocked(monkeypatch, "_maybe_recompute_export_price_forecast")
    monkeypatch.setattr(app, "_COORDINATOR_STOP_TIMEOUT_SECONDS", 0.2)
    host = SimpleNamespace(emsomat_online=True, stop=lambda: events.append("client stopped"))
    _start(host, tmp_path)
    assert started.wait(5)
    with caplog.at_level(logging.WARNING, logger="main"):
        app._stop_sparkplug((host,))
    assert "cannot be aborted" in caplog.text and events == ["client stopped"]
    release.set()
    app._coordinator_thread.join(timeout=5)
    assert events == ["client stopped", "step finished"], "nothing published after the client was stopped"


def test_real_host_sends_no_ncmd_after_it_was_taken_down(tmp_path, monkeypatch):
    started, release, _ = _blocked(monkeypatch, "_maybe_recompute_export_price_forecast")
    monkeypatch.setattr(app, "_publish_coordinator_sparkplug", lambda h, c, d: h.publish_coordinator_ncmd(()))
    monkeypatch.setattr(app, "_COORDINATOR_STOP_TIMEOUT_SECONDS", 0.1)
    host, client = _host()
    _start(host, tmp_path)
    assert started.wait(5)
    app._stop_sparkplug((host,))
    release.set()
    app._coordinator_thread.join(timeout=5)
    assert client.ncmd() == []


def test_stop_during_the_wait_for_emsomat_ends_at_once(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "_COORDINATOR_ONLINE_WAIT_SECONDS", 30.0)
    _blocked(monkeypatch, "_maybe_recompute_demand_forecast")
    host = SimpleNamespace(emsomat_online=False, stop=lambda: None)
    _start(host, tmp_path)
    time.sleep(0.1)
    started = time.perf_counter()
    app._stop_sparkplug((host,))
    assert time.perf_counter() - started < 2 and not app._coordinator_thread.is_alive()


def test_no_new_tasks_during_a_stop(tmp_path, monkeypatch, caplog):
    _, _, events = _blocked(monkeypatch, "_maybe_recompute_demand_forecast")
    host = SimpleNamespace(emsomat_online=True, stop=lambda: None)
    app._stop_sparkplug((host,))
    with caplog.at_level(logging.INFO, logger="main"):
        _start(host, tmp_path)
    assert app._coordinator_thread is None and "not started, shutting down" in caplog.text and events == []


def test_repeated_automatic_starts_keep_one_worker(tmp_path, monkeypatch):
    started, release, _ = _blocked(monkeypatch, "_maybe_recompute_demand_forecast")
    host = SimpleNamespace(emsomat_online=True, stop=lambda: None)
    for _ in range(10):
        _start(host, tmp_path)
    assert started.wait(5)
    workers = [t for t in threading.enumerate() if t.name == "shareomat-coordinator"]
    release.set()
    assert len(workers) == 1
