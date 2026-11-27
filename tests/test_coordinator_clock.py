# -*- coding: utf-8 -*-
"""Own clock for forecast and prices to Emsomat, and the engine status (P2a, B26/B27).

Until 0.6.33 the coordinator tasks only ran at the end of a settlement cycle
(the CSV run), and the engine status only became "ok" after such a run. Now the
coordinator runs every 15 minutes and at once when Emsomat comes online, and the
status is "ok" from startup on and is sent again after an MQTT reconnect.
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import main as app
from shareomat.config import MqttConfig
from shareomat.database.config_builder import IncompleteConfigError
from shareomat.ha import mqtt_runtime
from shareomat.leg_const import MQTT_STATUS_ERROR, MQTT_STATUS_OK, MQTT_STATUS_STARTING

INTERVAL = app._COORDINATOR_INTERVAL_SECONDS


@pytest.fixture(autouse=True)
def clean_coordinator():
    app._COORDINATOR_STOP.clear()
    app._SHUTDOWN.clear()
    yield
    app._COORDINATOR_STOP.clear()
    app._SHUTDOWN.clear()


# ── _coordinator_due ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("now", "last", "online", "was_online", "due"), [
    (100.0, None, False, False, False),                # Emsomat offline: nothing to send to
    (100.0, 50.0, False, True, False),                 # just went offline
    (100.0, None, True, False, True),                  # first time online
    (100.0, 99.0, True, False, True),                  # came back online: at once, even right after a run
    (100.0, None, True, True, True),                   # online, never sent yet (e.g. setup was incomplete)
    (100.0, 100.0 - INTERVAL + 1, True, True, False),  # online, interval not over
    (100.0, 100.0 - INTERVAL, True, True, True),       # online, interval over
])
def test_coordinator_due(now, last, online, was_online, due):
    assert app._coordinator_due(now, last, online, was_online) is due


# ── _coordinator_clock with a scripted timeline ──────────────────────────────


class _Timeline:
    """Replaces _COORDINATOR_STOP: each wait() moves to the next (online, now) step."""

    def __init__(self, host, steps):
        self._host = host
        self._steps = list(steps)
        self.now = 0.0

    def wait(self, timeout=None):
        if not self._steps:
            return True
        self._host.emsomat_online, self.now = self._steps.pop(0)
        return False

    def is_set(self):
        return False

    def clear(self):
        pass


def _run_clock(monkeypatch, steps, *, config=lambda db, rt: "config"):
    host = SimpleNamespace(emsomat_online=False)
    timeline = _Timeline(host, steps)
    started = []
    monkeypatch.setattr(app, "_COORDINATOR_STOP", timeline)
    monkeypatch.setattr(app, "time", SimpleNamespace(monotonic=lambda: timeline.now))
    monkeypatch.setattr(app, "build_leg_config", config)
    monkeypatch.setattr(app, "_start_coordinator_tasks", lambda h, c, db: started.append((timeline.now, c)))
    app._coordinator_clock(SimpleNamespace(), "db", host)
    return [now for now, _ in started], started


def test_clock_sends_nothing_while_emsomat_is_offline(monkeypatch):
    times, _ = _run_clock(monkeypatch, [(False, t) for t in (0, 5, INTERVAL, 2 * INTERVAL)])
    assert times == []


def test_clock_sends_at_once_when_emsomat_comes_online_then_every_interval(monkeypatch):
    steps = [(False, 0), (True, 5), (True, 10), (True, 5 + INTERVAL - 1), (True, 5 + INTERVAL),
             (True, 5 + INTERVAL + 5)]
    times, started = _run_clock(monkeypatch, steps)
    assert times == [5, 5 + INTERVAL]
    assert all(config == "config" for _, config in started)


def test_clock_sends_at_once_when_emsomat_comes_back(monkeypatch):
    steps = [(True, 0), (False, 10), (True, 20), (True, 30)]
    times, _ = _run_clock(monkeypatch, steps)
    assert times == [0, 20]


def test_clock_builds_the_configuration_fresh_each_time(monkeypatch):
    built = []

    def config(db, rt):
        built.append(db)
        return f"config-{len(built)}"

    _, started = _run_clock(monkeypatch, [(True, 0), (True, INTERVAL)], config=config)
    assert [c for _, c in started] == ["config-1", "config-2"]


def test_clock_retries_while_setup_is_incomplete(monkeypatch):
    calls = []

    def config(db, rt):
        calls.append(db)
        if len(calls) < 3:
            raise IncompleteConfigError("setup incomplete")
        return "config"

    times, _ = _run_clock(monkeypatch, [(True, 0), (True, 5), (True, 10), (True, 15)], config=config)
    assert times == [10]  # retried on every poll until the setup was complete, then on the interval


def test_clock_survives_an_unexpected_configuration_error(monkeypatch, caplog):
    def config(db, rt):
        raise RuntimeError("database locked")

    times, _ = _run_clock(monkeypatch, [(True, 0), (True, 5)], config=config)
    assert times == []
    assert "configuration could not be built" in caplog.text


def test_clock_stops_on_shutdown(monkeypatch):
    host = SimpleNamespace(emsomat_online=True)
    started = []
    monkeypatch.setattr(app, "_COORDINATOR_POLL_SECONDS", 0.01)
    monkeypatch.setattr(app, "build_leg_config", lambda db, rt: "config")
    monkeypatch.setattr(app, "_start_coordinator_tasks", lambda h, c, db: started.append(c))
    thread = threading.Thread(target=app._coordinator_clock, args=(SimpleNamespace(), "db", host), daemon=True)
    thread.start()
    app._COORDINATOR_STOP.set()
    thread.join(2)
    assert not thread.is_alive()


def test_clock_thread_is_a_named_daemon(monkeypatch):
    monkeypatch.setattr(app, "_COORDINATOR_POLL_SECONDS", 0.01)
    app._COORDINATOR_STOP.set()  # ends at the first wait
    app._start_coordinator_clock(SimpleNamespace(), "db", SimpleNamespace(emsomat_online=False))
    clocks = [t for t in threading.enumerate() if t.name == "shareomat-coordinator-clock"]
    for thread in clocks:
        assert thread.daemon
        thread.join(2)


# ── Engine status ────────────────────────────────────────────────────────────


class _FakePaho:
    """Just enough of paho's Client for create_client; loop_start 'connects'."""

    def __init__(self):
        self.published = []
        self._userdata = None
        self.on_connect = None

    def user_data_set(self, userdata):
        self._userdata = userdata

    def user_data_get(self):
        return self._userdata

    def will_set(self, *args, **kwargs):
        pass

    def reconnect_delay_set(self, **kwargs):
        pass

    def connect(self, *args, **kwargs):
        pass

    def loop_start(self):
        self.reconnect()

    def reconnect(self):
        self.on_connect(self, self._userdata, {}, 0)

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))

    def subscribe(self, *args, **kwargs):
        pass

    def status(self):
        return [payload for topic, payload, _ in self.published if topic.endswith("/status")]


@pytest.fixture
def mqtt_client(monkeypatch):
    fake = _FakePaho()
    monkeypatch.setattr(mqtt_runtime, "_PAHO_AVAILABLE", True)
    monkeypatch.setattr(mqtt_runtime, "_make_client", lambda client_id: fake)
    config = MqttConfig(enabled=True, broker="broker.example", port=1883, client_id="c")
    client = mqtt_runtime.create_client(config)
    assert client is fake
    return fake, config


def test_status_is_starting_before_shareomat_runs(mqtt_client):
    client, _ = mqtt_client
    assert client.status() == [MQTT_STATUS_STARTING]
    client.reconnect()
    assert client.status()[-1] == MQTT_STATUS_STARTING


def test_status_is_ok_once_running_and_after_a_reconnect(mqtt_client):
    client, config = mqtt_client
    mqtt_runtime.mark_running(client, config)
    assert client.status()[-1] == MQTT_STATUS_OK
    assert client.published[-1] == (f"{config.topic_prefix}/status", MQTT_STATUS_OK, True)
    client.reconnect()
    assert client.status()[-1] == MQTT_STATUS_OK  # was "starting" until 0.6.32


def test_reconnect_repeats_the_last_status_not_a_fixed_one(mqtt_client):
    client, config = mqtt_client
    mqtt_runtime.mark_running(client, config)
    mqtt_runtime.publish_status(client, MQTT_STATUS_ERROR, config)
    client.reconnect()
    assert client.status()[-1] == MQTT_STATUS_ERROR
    mqtt_runtime.publish_system_state(client, config, status=MQTT_STATUS_OK)
    client.reconnect()
    assert client.status()[-1] == MQTT_STATUS_OK


def test_status_memory_keeps_the_subscriptions(mqtt_client):
    client, config = mqtt_client
    subscriptions = list(client.user_data_get()["subscriptions"])
    mqtt_runtime.mark_running(client, config)
    assert client.user_data_get()["subscriptions"] == subscriptions
