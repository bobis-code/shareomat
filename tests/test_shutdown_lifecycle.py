# -*- coding: utf-8 -*-
"""Lifecycle of main.py, the web server and the Sparkplug host.

Real threads, real sockets, real SQLite files and the real billing release.
Replaced are only the MQTT client and the pauses that fix the timing.
"""
from __future__ import annotations

import http.client
import logging
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler
from types import SimpleNamespace

import pytest

import main as app
from shareomat.config import MqttConfig, SparkplugConfig
from shareomat.database import billing
from shareomat.database.consumption_forecasts import latest_computed_at
from shareomat.sparkplug import host as host_module
from shareomat.sparkplug.coordinator_store import CoordinatorStoreError, coordinator_db_path, ensure_separate_from
from shareomat.sparkplug.host import SparkplugHost
from shareomat.web import login, server as web_module
from shareomat.web.server import WebServer
from tests.test_auth import _setup_admin
from tests.test_billing_atomicity import case  # noqa: F401
from tests.test_config import _leg_config


@pytest.fixture(autouse=True)
def clean_lifecycle(monkeypatch):
    previous = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    monkeypatch.setattr(app, "_COORDINATOR_ONLINE_WAIT_SECONDS", 0.0)
    app._SHUTDOWN.clear()
    app._CLEANUP_STARTED.clear()
    app._COORDINATOR_STOP.clear()
    yield
    if app._coordinator_thread is not None:
        app._coordinator_thread.join(timeout=15)
    app._coordinator_thread = None
    app._SHUTDOWN.clear()
    app._CLEANUP_STARTED.clear()
    app._COORDINATOR_STOP.clear()
    signal.signal(signal.SIGTERM, previous[0])
    signal.signal(signal.SIGINT, previous[1])


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serving(port, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
            return True
        except OSError:
            time.sleep(0.02)
    return False


def _refuses(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return False
    except OSError:
        return True


class Part:
    """A started component that records how often and when it was stopped."""

    def __init__(self, name, log):
        self.name, self.log = name, log

    def stop(self):
        self.log.append(self.name)


# ── main.py: signals during start and first cycle ────────────────────────────


@pytest.fixture
def started_main(tmp_path, monkeypatch):
    config = tmp_path / "leg_config.yaml"
    config.write_text(
        "paths:\n"
        + "".join(f"  {n}: {tmp_path / n}\n".replace("\\", "/") for n in ("inbox", "archive", "reports", "state"))
        + "web:\n  enabled: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(app, "_RUNTIME_CONFIG_PATH", config)
    monkeypatch.setenv("SHAREOMAT_DB_PATH", str(tmp_path / "shareomat.db"))
    monkeypatch.setattr(app, "_update_web_state", lambda *a, **k: None)
    log = []
    return log


def test_sigterm_during_initialisation_runs_the_cleanup_of_what_started(started_main, monkeypatch):
    log = started_main

    def live(*args):
        signal.raise_signal(signal.SIGTERM)
        return Part("live", log)

    monkeypatch.setattr(app, "_setup_live", live)
    monkeypatch.setattr(app, "_setup_sparkplug", lambda *a: pytest.fail("must not start after the signal"))
    monkeypatch.setattr(app, "run", lambda *a, **k: pytest.fail("no settlement after the signal"))
    app.main()
    assert log == ["live"] and app._SHUTDOWN.is_set()


def test_sigterm_during_the_first_cycle_lets_it_finish_then_cleans_up(started_main, monkeypatch):
    log = started_main
    monkeypatch.setattr(app, "_setup_live", lambda *a: Part("live", log))
    monkeypatch.setattr(app, "_setup_sparkplug", lambda *a: (None, None, None, None))
    monkeypatch.setattr(app, "build_leg_config", lambda *a: _leg_config())
    monkeypatch.setattr(app, "_run_daemon", lambda *a, **k: pytest.fail("no daemon after the signal"))

    def settlement(config, mqtt_client=None, db_path=None):
        signal.raise_signal(signal.SIGTERM)
        log.append("settlement finished")

    monkeypatch.setattr(app, "run", settlement)
    app.main()
    assert log == ["settlement finished", "live"]


def test_ctrl_c_takes_the_same_path(started_main, monkeypatch):
    log = started_main

    def live(*args):
        signal.raise_signal(signal.SIGINT)
        return Part("live", log)

    monkeypatch.setattr(app, "_setup_live", live)
    monkeypatch.setattr(app, "_setup_sparkplug", lambda *a: pytest.fail("must not start"))
    app.main()
    assert log == ["live"]


def test_second_signal_ends_a_stuck_start_and_cleanup_still_runs(started_main, monkeypatch):
    log = started_main

    def live(*args):
        signal.raise_signal(signal.SIGTERM)
        signal.raise_signal(signal.SIGTERM)
        return None

    monkeypatch.setattr(app, "_setup_live", live)
    app.main()
    assert app._CLEANUP_STARTED.is_set()


def test_signal_during_cleanup_does_not_interrupt_it():
    app._SHUTDOWN.set()
    app._CLEANUP_STARTED.set()
    app._request_shutdown(signal.SIGTERM, None)


def test_no_settlement_starts_once_a_shutdown_was_requested(tmp_path, monkeypatch):
    monkeypatch.setattr(app, "run", lambda *a, **k: pytest.fail("no settlement during a shutdown"))
    app._SHUTDOWN.set()
    assert app._run_safe_cycle(SimpleNamespace(), tmp_path / "x.db", None) is False


def test_no_coordinator_starts_once_a_shutdown_was_requested(tmp_path):
    app._SHUTDOWN.set()
    app._start_coordinator_tasks(SimpleNamespace(emsomat_online=True), object(), tmp_path / "x.db")
    assert app._coordinator_thread is None


# ── Shutdown sequence ────────────────────────────────────────────────────────


def test_newest_first_only_started_and_only_once():
    log = []
    sequence = app._ShutdownSequence()
    sequence.add("mqtt", lambda: log.append("mqtt"))
    sequence.add("web", lambda: log.append("web"))
    sequence.run()
    sequence.run()
    assert log == ["web", "mqtt"]


def test_a_failing_step_does_not_stop_the_others(caplog):
    log = []
    sequence = app._ShutdownSequence()
    sequence.add("mqtt", lambda: log.append("mqtt"))

    def broken():
        raise RuntimeError("kaputt")

    sequence.add("web", broken)
    with caplog.at_level(logging.ERROR, logger="main"):
        sequence.run()
    assert log == ["mqtt"] and "Shutdown step failed: web" in caplog.text


def test_running_settlement_is_waited_for_within_the_deadline():
    app._shutdown_deadline = time.monotonic() + 5
    assert app._RUN_LOCK.acquire(blocking=False)
    threading.Timer(0.3, app._RUN_LOCK.release).start()
    started = time.perf_counter()
    app._wait_for_running_settlement()
    assert 0.2 < time.perf_counter() - started < 3


def test_a_stuck_settlement_does_not_hold_up_the_shutdown(caplog):
    app._shutdown_deadline = time.monotonic() + 0.1
    assert app._RUN_LOCK.acquire(blocking=False)
    try:
        with caplog.at_level(logging.WARNING, logger="main"):
            app._wait_for_running_settlement()
    finally:
        app._RUN_LOCK.release()
    assert "still active" in caplog.text


# ── Sparkplug host: check, publish and stop ──────────────────────────────────


class Client:
    def __init__(self):
        self.events = []
        self.block_ncmd = None
        self.entered = threading.Event()

    def publish(self, topic, payload, qos=0, retain=False):
        if "/NCMD/" in topic and self.block_ncmd is not None:
            self.entered.set()
            assert self.block_ncmd.wait(10)
        self.events.append("ncmd" if "/NCMD/" in topic else "state")

    def loop_stop(self):
        self.events.append("loop_stop")

    def disconnect(self):
        self.events.append("disconnect")


def _host():
    client = Client()
    host = SparkplugHost(SparkplugConfig(enabled=True, emsomat_edge_node_id="n"),
                         MqttConfig(enabled=True, broker="b", port=1883, client_id="c"), client=client)
    host._connected.set()
    host._emsomat_online = True
    return host, client


def test_stop_between_connection_check_and_publish():
    """The publish has passed its check and is under way when stop() arrives."""
    host, client = _host()
    client.block_ncmd = threading.Event()
    publisher = threading.Thread(target=host.publish_coordinator_ncmd, args=((),))
    publisher.start()
    assert client.entered.wait(5)
    stopper = threading.Thread(target=host.stop)
    stopper.start()
    time.sleep(0.1)
    assert client.events == [], "stop waits for the publish that is under way"
    client.block_ncmd.set()
    publisher.join(5)
    stopper.join(5)
    assert client.events == ["ncmd", "state", "loop_stop", "disconnect"]


def test_nothing_is_published_after_stop():
    host, client = _host()
    host.stop()
    count = len(client.events)
    host.publish_coordinator_ncmd(())
    host.request_rebirth()
    assert len(client.events) == count


def test_stop_twice_tears_down_once():
    host, client = _host()
    host.stop()
    host.stop()
    assert client.events == ["state", "loop_stop", "disconnect"]


def test_stop_gives_up_on_a_stuck_publish(monkeypatch, caplog):
    monkeypatch.setattr(host_module, "_STOP_LOCK_TIMEOUT_S", 0.1)
    host, client = _host()
    client.block_ncmd = threading.Event()
    publisher = threading.Thread(target=host.publish_coordinator_ncmd, args=((),), daemon=True)
    publisher.start()
    assert client.entered.wait(5)
    started = time.perf_counter()
    with caplog.at_level(logging.WARNING):
        host.stop()
    assert time.perf_counter() - started < 2 and "did not finish in time" in caplog.text
    client.block_ncmd.set()
    publisher.join(5)
    host.publish_coordinator_ncmd(())
    assert client.events.count("ncmd") == 1


def test_both_waits_expire_and_nothing_is_sent_afterwards(tmp_path, monkeypatch):
    """A hanging price request outlasts the coordinator wait, a stuck publish the host wait."""
    monkeypatch.setattr(app, "_COORDINATOR_STOP_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(host_module, "_STOP_LOCK_TIMEOUT_S", 0.1)
    started_event, release = threading.Event(), threading.Event()

    def hanging(db):
        started_event.set()
        release.wait(20)

    monkeypatch.setattr(app, "_maybe_recompute_demand_forecast", lambda d: None)
    monkeypatch.setattr(app, "_maybe_recompute_export_price_forecast", hanging)
    monkeypatch.setattr(app, "_publish_coordinator_sparkplug", lambda h, c, d: h.publish_coordinator_ncmd(()))
    host, client = _host()
    app._start_coordinator_tasks(host, object(), tmp_path / "x.db")
    assert started_event.wait(5)
    started = time.perf_counter()
    app._stop_sparkplug((host,))
    assert time.perf_counter() - started < 2
    release.set()
    app._coordinator_thread.join(5)
    assert "ncmd" not in client.events


# ── Web server ───────────────────────────────────────────────────────────────


class _Slow(BaseHTTPRequestHandler):
    entered = None
    release = None

    def do_GET(self):
        type(self).entered.set()
        assert type(self).release.wait(10)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"fertig")

    def log_message(self, *args):
        pass


@pytest.fixture
def slow_server(monkeypatch):
    _Slow.entered, _Slow.release = threading.Event(), threading.Event()
    monkeypatch.setattr(web_module, "_Handler", _Slow)
    port = _free_port()
    web = WebServer(port=port)
    web.start()
    assert _serving(port)
    yield web, port
    _Slow.release.set()
    web.stop(timeout=5)


def _slow_request(port, results):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    connection.request("GET", "/")
    response = connection.getresponse()
    results.append((response.status, response.read()))
    connection.close()


def test_stop_finishes_a_running_request_and_takes_no_new_ones(slow_server):
    web, port = slow_server
    results = []
    client = threading.Thread(target=_slow_request, args=(port, results))
    client.start()
    assert _Slow.entered.wait(5)
    outcome = []
    stopper = threading.Thread(target=lambda: outcome.append(web.stop(timeout=10)))
    stopper.start()
    deadline = time.monotonic() + 3
    while not _refuses(port) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert stopper.is_alive() and _refuses(port)
    _Slow.release.set()
    stopper.join(10)
    client.join(10)
    assert outcome == [True] and results == [(200, b"fertig")]


def test_deadline_ends_the_wait_and_is_logged(slow_server, caplog):
    web, port = slow_server
    results = []
    threading.Thread(target=_slow_request, args=(port, results), daemon=True).start()
    assert _Slow.entered.wait(5)
    started = time.perf_counter()
    with caplog.at_level(logging.WARNING):
        assert web.stop(timeout=0.2) is False
    assert time.perf_counter() - started < 3 and "still running" in caplog.text


def test_a_blocked_client_cannot_hold_the_shutdown(slow_server):
    web, port = slow_server
    idle = socket.create_connection(("127.0.0.1", port))      # says nothing at all
    try:
        time.sleep(0.2)
        started = time.perf_counter()
        web.stop(timeout=0.3)
        assert time.perf_counter() - started < 3
    finally:
        idle.close()


def test_stop_is_safe_twice_and_before_the_server_runs(monkeypatch):
    never_started = WebServer(port=_free_port())
    assert never_started.stop(timeout=0.1) is True
    assert never_started.stop(timeout=0.1) is True


def test_stop_right_after_start_never_hangs():
    for _ in range(20):
        web = WebServer(port=_free_port())
        web.start()
        done = []
        stopper = threading.Thread(target=lambda: done.append(web.stop(timeout=1)), daemon=True)
        stopper.start()
        stopper.join(5)
        assert done, "stop returned"


def test_restart_on_the_same_port_after_a_controlled_shutdown(slow_server, monkeypatch):
    web, port = slow_server
    _Slow.release.set()
    assert web.stop(timeout=5) is True
    second = WebServer(port=port)
    second.start()
    try:
        assert _serving(port)
        results = []
        _slow_request(port, results)
        assert results == [(200, b"fertig")]
    finally:
        second.stop(timeout=5)


@pytest.fixture
def logged_in(case, monkeypatch):      # noqa: F811
    db, runtime, run_id, source = case
    monkeypatch.setattr(login, "FAILED_LOGIN_DELAY_SECONDS", 0)
    monkeypatch.setattr(login, "ACCOUNT_DELAY_FACTOR", 0)
    port = _free_port()
    web = WebServer(port=port)
    web.start()
    assert _serving(port)
    browser = _setup_admin({"port": port, "db": db})
    yield SimpleNamespace(db=db, runtime=runtime, run_id=run_id, port=port, web=web, browser=browser)
    web.stop(timeout=5)


def _pause_release(monkeypatch):
    original = billing.compute_billing_preview
    paused, resume = threading.Event(), threading.Event()

    def pausing(*args, **kwargs):
        preview = original(*args, **kwargs)
        paused.set()
        assert resume.wait(30)
        return preview

    return original, paused, resume, pausing


def test_shutdown_during_a_real_release_lets_it_complete(logged_in, monkeypatch):
    env = logged_in
    token = env.browser.page_csrf(f"/billing/{env.run_id}")
    _, paused, resume, pausing = _pause_release(monkeypatch)
    monkeypatch.setattr(billing, "compute_billing_preview", pausing)
    results = []
    client = threading.Thread(target=lambda: results.append(env.browser.post(f"/billing/{env.run_id}/release", {"csrf_token": token})))
    client.start()
    assert paused.wait(20)
    outcome = []
    stopper = threading.Thread(target=lambda: outcome.append(env.web.stop(timeout=20)))
    stopper.start()
    time.sleep(0.4)
    assert stopper.is_alive(), "the stop waits for the release"
    assert billing.get_billing_run(env.db, env.run_id).run.status == "draft"
    resume.set()
    stopper.join(25)
    client.join(25)
    assert outcome == [True] and results[0][0] == 303
    assert billing.get_billing_run(env.db, env.run_id).run.status == "released"


def test_web_and_coordinator_shutdown_together(logged_in, monkeypatch):
    env = logged_in
    host, client = _host()
    token = env.browser.page_csrf(f"/billing/{env.run_id}")
    _, paused, resume, pausing = _pause_release(monkeypatch)
    monkeypatch.setattr(billing, "compute_billing_preview", pausing)
    coordinator_started, coordinator_release = threading.Event(), threading.Event()

    def hanging(db):
        coordinator_started.set()
        coordinator_release.wait(20)

    monkeypatch.setattr(app, "_maybe_recompute_demand_forecast", hanging)
    monkeypatch.setattr(app, "_maybe_recompute_export_price_forecast", lambda d: None)
    monkeypatch.setattr(app, "_publish_coordinator_sparkplug", lambda h, c, d: h.publish_coordinator_ncmd(()))
    monkeypatch.setattr(app, "_COORDINATOR_STOP_TIMEOUT_SECONDS", 0.2)
    app._start_coordinator_tasks(host, object(), env.db)
    assert coordinator_started.wait(5)

    results = []
    browser = threading.Thread(target=lambda: results.append(env.browser.post(f"/billing/{env.run_id}/release", {"csrf_token": token})))
    browser.start()
    assert paused.wait(20)

    order = []
    sequence = app._ShutdownSequence()
    sequence.add("sparkplug", lambda: (order.append("sparkplug"), app._stop_sparkplug((host,))))
    sequence.add("settlement", app._wait_for_running_settlement)
    sequence.add("web", lambda: (order.append("web"), env.web.stop(timeout=app._drain_seconds_left())))
    runner = threading.Thread(target=sequence.run)
    runner.start()
    time.sleep(0.3)
    assert order == ["web"], "Sparkplug goes only after the web requests"
    resume.set()
    runner.join(25)
    browser.join(25)
    coordinator_release.set()
    app._coordinator_thread.join(5)
    assert order == ["web", "sparkplug"]
    assert billing.get_billing_run(env.db, env.run_id).run.status == "released"
    assert "ncmd" not in client.events and client.events[-2:] == ["loop_stop", "disconnect"]


def test_forced_process_end_leaves_no_partial_transaction(tmp_path):
    """What the end of the deadline or SIGKILL means for a running write."""
    db = tmp_path / "t.db"
    con = sqlite3.connect(db)
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("CREATE TABLE t (x)")
    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    con.close()
    code = ("import sqlite3, os, sys\n"
            "c = sqlite3.connect(sys.argv[1], isolation_level=None)\n"
            "c.execute('BEGIN IMMEDIATE')\n"
            "c.execute('INSERT INTO t VALUES (2)')\n"
            "os._exit(0)\n")
    subprocess.run([sys.executable, "-c", code, str(db)], check=True, timeout=30)
    con = sqlite3.connect(db)
    try:
        assert con.execute("SELECT count(*) FROM t").fetchone()[0] == 1
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        con.close()


# ── coordinator.db path ──────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["coordinator.db", "COORDINATOR.DB"])
def test_main_database_named_like_the_store_stays_separate(tmp_path, name):
    db = tmp_path / name
    store = coordinator_db_path(db)
    assert store.name.casefold() != name.casefold() and store.parent == tmp_path


def test_store_that_is_a_link_to_the_main_database_is_refused(tmp_path):
    db = tmp_path / "shareomat.db"
    db.write_bytes(b"x")
    store = coordinator_db_path(db)
    os.link(db, store)
    with pytest.raises(CoordinatorStoreError):
        ensure_separate_from(db, store)


def test_coordinator_writes_nothing_when_the_store_is_the_main_database(tmp_path, caplog):
    from shareomat.database.sqlite import init_db

    db = tmp_path / "shareomat.db"
    init_db(db)
    os.link(db, coordinator_db_path(db))
    host, client = _host()
    with caplog.at_level(logging.ERROR, logger="main"):
        app._run_coordinator_tasks(host, _leg_config(), db)
    assert "coordinator step failed" in caplog.text and "ncmd" not in client.events
    assert latest_computed_at(db, scope="leg") is None


def test_coordinator_data_survive_a_controlled_shutdown(tmp_path):
    from shareomat.database.sqlite import init_db

    db = tmp_path / "shareomat.db"
    init_db(db)
    host, _ = _host()
    app._run_coordinator_tasks(host, _leg_config(), db)
    first = latest_computed_at(coordinator_db_path(db), scope="leg")
    assert first is not None
    app._stop_sparkplug((host,))
    app._COORDINATOR_STOP.clear()
    assert latest_computed_at(app._coordinator_store(db), scope="leg") == first
