# -*- coding: utf-8 -*-
"""Regression tests for the audit findings AUD-01, AUD-05, AUD-06 and AUD-13. No broker, no HA."""
from __future__ import annotations

import dataclasses
import json
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from tests.test_config import _leg_config

import main as app
import shareomat.web.pages
from shareomat.config import MqttConfig, RuntimeConfig, SparkplugConfig, WanConfig
from shareomat.database.community import save_community
from shareomat.database.live_sources import create_live_source, list_live_sources
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.ha.live_sensors import LiveHaPublisher, build_states, publish_live_discovery
from shareomat.live.model import LiveParams
from shareomat.live.service import LiveStatusService
from shareomat.models.community import Community
from shareomat.models.live_source import ADAPTER_EMSOMAT_LOCAL
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.sparkplug.vendor.datatype import DataType
from shareomat.sparkplug.vendor.metric import Metric
from shareomat.web import server as web_server
from shareomat.web.rendering import FormError, RequestContext

NOW = datetime.now(timezone.utc)
NODE = "EMS-A"
KEY = f"{ADAPTER_EMSOMAT_LOCAL}:{NODE}"
MQTT = MqttConfig(enabled=True, broker="b", port=1883, client_id="c")


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    save_community(path, Community(community_id="LEG-1", name="Test"))
    create_participant(path, Participant("P1", "Haus 1"))
    create_meter(path, Meter(meter_id="M1", participant_id="P1", label="Haus 1", role="consumer"))
    return path


@pytest.fixture(autouse=True)
def no_wait_and_clean(monkeypatch):
    monkeypatch.setattr(app, "_COORDINATOR_ONLINE_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(app, "_reported_communication_problem", "")
    app._COORDINATOR_STOP.clear()
    yield
    if app._coordinator_thread is not None:
        app._coordinator_thread.join(timeout=15)
    app._coordinator_thread = None
    app._COORDINATOR_STOP.clear()


def _runtime(tmp_path=None, **overrides):
    runtime = RuntimeConfig.__new__(RuntimeConfig)
    runtime.mqtt = MQTT
    runtime.sparkplug = overrides.get("sparkplug", SparkplugConfig(enabled=False))
    runtime.wan = overrides.get("wan", WanConfig(enabled=False))
    runtime.paths = SimpleNamespace(inbox=tmp_path or SimpleNamespace(exists=lambda: False), reports=tmp_path or SimpleNamespace(exists=lambda: False))
    return runtime


class Host:
    def __init__(self, online=True):
        self.emsomat_online = online


def _steps(monkeypatch, *, fail=()):
    """Stub the three coordinator steps, recording their order."""
    calls = []

    def step(name):
        def run(*args):
            calls.append(name)
            if name in fail:
                raise RuntimeError(f"{name} kaputt")
        return run

    monkeypatch.setattr(app, "_maybe_recompute_demand_forecast", step("forecast"))
    monkeypatch.setattr(app, "_maybe_recompute_export_price_forecast", step("price"))
    monkeypatch.setattr(app, "_publish_coordinator_sparkplug", step("publish"))
    return calls


def _cycle_stubs(monkeypatch, config=None):
    runs = []
    monkeypatch.setattr(app, "build_leg_config", lambda db, rt: config if config is not None else object())
    if config is None:
        monkeypatch.setattr(app, "validate_leg_config", lambda c: None)
    monkeypatch.setattr(app, "run", lambda c, mqtt_client=None, db_path=None: runs.append(c))
    monkeypatch.setattr(app, "_update_web_state", lambda *a, **k: None)
    return runs


# ── 1. Automatic Sparkplug reception and live processing ─────────────────────


def test_sparkplug_values_reach_the_live_layer_automatically(db_path, monkeypatch):
    source_id = create_live_source(db_path, meter_id="M1", adapter=ADAPTER_EMSOMAT_LOCAL, edge_node_id=NODE, max_power_w=17250)
    service = LiveStatusService(db_path, LiveParams())
    captured = {}

    class FakeHost:
        request_rebirth = None

        def __init__(self, *a, **kwargs):
            captured.update(kwargs)

        def start(self):
            return True

    class FakeRelay:
        def __init__(self, *a, **k):
            pass

        def start(self):
            return True

    monkeypatch.setattr(app, "SparkplugHost", FakeHost)
    monkeypatch.setattr(app, "ParticipantRelay", FakeRelay)
    host, *_ = app._setup_sparkplug(_runtime(sparkplug=SparkplugConfig(enabled=True, emsomat_edge_node_id=NODE)), service)
    assert isinstance(host, FakeHost)

    ms = int(NOW.timestamp() * 1000)
    metrics = (Metric(timestamp=ms, name="Market/ImportPowerNow", datatype=DataType.DOUBLE, value=0.0),
               Metric(timestamp=ms, name="Market/ExportPowerNow", datatype=DataType.DOUBLE, value=81.0))
    captured["live_sink"].on_birth(SimpleNamespace(metrics=metrics, timestamp=ms), 1)
    service.process_pending()
    reading = service.source_reading(source_id)
    assert (reading.quality, reading.import_w, reading.export_w) == ("ok", 0.0, 81.0)


# ── 2 and 3. Coordinator, forecast and price run by themselves, failures stay apart ──


def test_coordinator_steps_run_automatically_with_emsomat_online(db_path, monkeypatch):
    calls = _steps(monkeypatch)
    app._run_coordinator_tasks(Host(), object(), db_path)
    assert calls == ["forecast", "price", "publish"]


def test_nothing_runs_without_an_emsomat_session(db_path, monkeypatch):
    calls = _steps(monkeypatch)
    app._run_coordinator_tasks(Host(online=False), object(), db_path)
    assert calls == []


def test_waits_briefly_for_the_birth_after_a_start(db_path, monkeypatch):
    calls = _steps(monkeypatch)
    monkeypatch.setattr(app, "_COORDINATOR_ONLINE_WAIT_SECONDS", 5.0)
    host = Host(online=False)
    threading.Timer(0.3, lambda: setattr(host, "emsomat_online", True)).start()
    app._run_coordinator_tasks(host, object(), db_path)
    assert calls == ["forecast", "price", "publish"]


@pytest.mark.parametrize("failing", ["forecast", "price", "publish"])
def test_a_failing_step_is_logged_and_stops_no_other(db_path, monkeypatch, caplog, failing):
    calls = _steps(monkeypatch, fail=(failing,))
    with caplog.at_level(logging.ERROR, logger="main"):
        app._run_coordinator_tasks(Host(), object(), db_path)
    assert calls == ["forecast", "price", "publish"]
    assert "coordinator step failed" in caplog.text


def test_overlapping_coordinator_runs_are_skipped(db_path, monkeypatch):
    calls = _steps(monkeypatch)
    assert app._COORDINATOR_LOCK.acquire(blocking=False)
    try:
        app._run_coordinator_tasks(Host(), object(), db_path)
    finally:
        app._COORDINATOR_LOCK.release()
    assert calls == []


def test_real_price_step_without_token_stays_off(db_path, monkeypatch):
    monkeypatch.setattr(app, "fetch_and_persist_day_ahead_prices", lambda *a, **k: pytest.fail("no token, no fetch"))
    app._maybe_recompute_export_price_forecast(db_path)


# ── 4. Billing does not depend on Sparkplug, WAN or the coordinator ──────────


def _config_with(**overrides):
    return dataclasses.replace(_leg_config(), **overrides)


@pytest.mark.parametrize("sparkplug, wan", [
    (SparkplugConfig(enabled=False), WanConfig(enabled=True, broker="b", group_id="g", edge_node_id="e", own_participant_id="P1")),
    (SparkplugConfig(enabled=True, emsomat_edge_node_id=""), WanConfig(enabled=False)),
    (SparkplugConfig(enabled=True, emsomat_edge_node_id="n"), WanConfig(enabled=True, broker="", group_id="g", edge_node_id="e", own_participant_id="P1")),
    (SparkplugConfig(enabled=True, emsomat_edge_node_id="n"), WanConfig(enabled=True, broker="b", group_id="g", edge_node_id="e", own_participant_id="UNBEKANNT")),
])
def test_invalid_communication_settings_do_not_stop_billing(tmp_path, monkeypatch, caplog, sparkplug, wan):
    runs = _cycle_stubs(monkeypatch, _config_with(sparkplug=sparkplug, wan=wan))
    with caplog.at_level(logging.ERROR, logger="main"):
        assert app._run_safe_cycle(_runtime(tmp_path), tmp_path / "x.db", None) is True
    assert len(runs) == 1
    assert "billing continues" in caplog.text


def test_a_billing_error_still_stops_the_run(tmp_path, monkeypatch):
    tariff = dataclasses.replace(_leg_config().tariff, grid_rate_chf_kwh=Decimal("-1"))
    runs = _cycle_stubs(monkeypatch, _config_with(tariff=tariff))
    assert app._run_safe_cycle(_runtime(tmp_path), tmp_path / "x.db", None) is False
    assert runs == []


def test_billing_error_is_reported_together_with_a_communication_error(tmp_path, monkeypatch):
    tariff = dataclasses.replace(_leg_config().tariff, grid_rate_chf_kwh=Decimal("-1"))
    runs = _cycle_stubs(monkeypatch, _config_with(tariff=tariff, wan=WanConfig(enabled=True)))
    assert app._run_safe_cycle(_runtime(tmp_path), tmp_path / "x.db", None) is False
    assert runs == []


def test_the_communication_rules_themselves_are_unchanged():
    with pytest.raises(ValueError, match="wan.enabled is true but sparkplug.enabled is false"):
        app.validate_leg_config(_config_with(wan=WanConfig(enabled=True, broker="b", group_id="g", edge_node_id="e", own_participant_id="P1")))


@pytest.mark.parametrize("wan, gap", [
    (WanConfig(enabled=True, broker="", group_id="g", edge_node_id="e", own_participant_id="P1"), "wan.broker"),
    (WanConfig(enabled=True, broker="b", group_id=" ", edge_node_id="e", own_participant_id="P1"), "wan.group_id"),
])
def test_incomplete_wan_is_not_started(monkeypatch, caplog, wan, gap):
    class Part:
        request_rebirth = None

        def __init__(self, *a, **k):
            pass

        def start(self):
            return True

    monkeypatch.setattr(app, "ParticipantRelay", Part)
    monkeypatch.setattr(app, "SparkplugHost", Part)
    monkeypatch.setattr(app, "build_wan_uplink", lambda *a, **k: pytest.fail("WAN must not start"))
    monkeypatch.setattr(app, "WanDownlink", lambda *a, **k: pytest.fail("WAN must not start"))
    with caplog.at_level(logging.ERROR, logger="main"):
        host, relay, uplink, downlink = app._setup_sparkplug(_runtime(sparkplug=SparkplugConfig(enabled=True, emsomat_edge_node_id="n"), wan=wan))
    assert host is not None and uplink is None and downlink is None
    assert gap in caplog.text


def test_sparkplug_without_node_is_not_started(caplog):
    with caplog.at_level(logging.ERROR, logger="main"):
        assert app._setup_sparkplug(_runtime(sparkplug=SparkplugConfig(enabled=True))) == (None, None, None, None)
    assert "emsomat_edge_node_id" in caplog.text


def test_coordinator_runs_outside_the_settlement_lock(tmp_path, monkeypatch):
    done = threading.Event()
    cycle_returned = threading.Event()
    lock_free = []
    _cycle_stubs(monkeypatch)

    def publish(*args):
        # The cycle finishes without waiting for this work, which then runs with the lock free.
        assert cycle_returned.wait(timeout=5)
        free = app._RUN_LOCK.acquire(blocking=False)
        if free:
            app._RUN_LOCK.release()
        lock_free.append(free)
        done.set()

    _steps(monkeypatch)
    monkeypatch.setattr(app, "_publish_coordinator_sparkplug", publish)
    assert app._run_safe_cycle(_runtime(tmp_path), tmp_path / "x.db", None, Host()) is True
    cycle_returned.set()
    assert done.wait(timeout=5) and lock_free == [True]


def test_hanging_price_server_does_not_block_the_next_run(tmp_path, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    runs = _cycle_stubs(monkeypatch)
    _steps(monkeypatch)

    def hanging(db):
        started.set()
        release.wait(timeout=10)

    monkeypatch.setattr(app, "_maybe_recompute_export_price_forecast", hanging)
    try:
        assert app._run_safe_cycle(_runtime(tmp_path), tmp_path / "x.db", None, Host()) is True
        assert started.wait(timeout=5)
        assert app._run_safe_cycle(_runtime(tmp_path), tmp_path / "x.db", None, Host()) is True
        assert len(runs) == 2
    finally:
        release.set()


def test_failing_thread_start_does_not_touch_the_run(tmp_path, monkeypatch):
    runs = _cycle_stubs(monkeypatch)

    class Broken:
        def __init__(self, *a, **k):
            raise RuntimeError("keine Threads mehr")

    monkeypatch.setattr(app.threading, "Thread", Broken)
    assert app._run_safe_cycle(_runtime(tmp_path), tmp_path / "x.db", None, Host()) is True
    assert len(runs) == 1


# ── 5. Start with an unavailable live module ─────────────────────────────────


def test_start_with_an_unavailable_live_module(tmp_path, monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "shareomat.live.service", None)
    with caplog.at_level(logging.ERROR, logger="main"):
        assert app._setup_live(tmp_path / "shareomat.db", _runtime(), None) is None
    assert "Live-Energie is not available" in caplog.text


def test_main_imports_nothing_of_the_live_package_at_module_level():
    source = open(app.__file__, encoding="utf-8").read()
    module_level = [line for line in source.splitlines() if line.startswith(("from ", "import "))]
    assert not any("shareomat.live" in line or "live_sensors" in line or "live_source" in line for line in module_level)


def test_broken_adapters_leave_sparkplug_running_without_live(monkeypatch, caplog):
    monkeypatch.setitem(sys.modules, "shareomat.live.adapters.sparkplug", None)
    captured = {}

    class Part:
        request_rebirth = None

        def __init__(self, *a, **kwargs):
            captured.update(kwargs)

        def start(self):
            return True

    monkeypatch.setattr(app, "ParticipantRelay", Part)
    monkeypatch.setattr(app, "SparkplugHost", Part)
    with caplog.at_level(logging.ERROR, logger="main"):
        host, *_ = app._setup_sparkplug(_runtime(sparkplug=SparkplugConfig(enabled=True, emsomat_edge_node_id="n")), object())
    assert host is not None and captured["live_sink"] is None
    assert "adapters could not be loaded" in caplog.text


def test_admin_server_survives_a_broken_live_page(monkeypatch, caplog):
    monkeypatch.delattr(shareomat.web.pages, "live_energy", raising=False)
    monkeypatch.setitem(sys.modules, "shareomat.web.pages.live_energy", None)
    with caplog.at_level(logging.ERROR):
        page = web_server._load_live_energy_page()
    assert page is web_server._LiveEnergyUnavailable
    assert "could not be loaded" in caplog.text
    ctx = RequestContext(method="POST", ingress_path="", form={}, query={})
    ctx.segments = []
    with pytest.raises(FormError):
        page.handle_post(ctx)


# ── 6. HA sensors: stale data, restart, reconnect ────────────────────────────


class Client:
    def __init__(self, connected=True):
        self.connected = connected
        self.published = []

    def is_connected(self):
        return self.connected

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, retain))


def _live(db_path, clock):
    create_live_source(db_path, meter_id="M1", adapter=ADAPTER_EMSOMAT_LOCAL, edge_node_id=NODE, max_power_w=17250)
    from shareomat.database.live_sources import activate_live_source
    activate_live_source(db_path, list_live_sources(db_path)[0].id)
    return LiveStatusService(db_path, LiveParams(), clock=clock)


def _feed(service, imp, exp, at):
    from shareomat.live.model import RawSample, RawValue, SessionStart
    ms = int(at.timestamp() * 1000)
    service.submit(SessionStart(ADAPTER_EMSOMAT_LOCAL, KEY, "s"))
    service.submit(RawSample(ADAPTER_EMSOMAT_LOCAL, KEY, "s", at, import_value=RawValue(imp, timestamp_ms=ms),
                             export_value=RawValue(exp, timestamp_ms=ms), payload_timestamp_ms=ms, time_origin="ha_last_reported"))
    service.process_pending()


def test_every_sensor_expires_when_updates_stop():
    client = Client()
    publish_live_discovery(client, MQTT)
    configs = [json.loads(p) for t, p, _ in client.published if t.endswith("/config")]
    assert configs and all(c["expire_after"] == 180 for c in configs)


def test_expiry_is_longer_than_the_full_publish_interval():
    from shareomat.ha import live_sensors
    assert live_sensors._EXPIRE_AFTER_S >= 2 * live_sensors._REFRESH_S


def test_values_are_not_retained_and_old_retained_values_are_removed(db_path):
    client = Client()
    publish_live_discovery(client, MQTT)
    cleared = {t for t, p, retain in client.published if p == "" and retain}
    assert "shareomat/live/own/export_w" in cleared and "shareomat/live/leg/status/attributes" in cleared
    assert all(retain for t, _, retain in client.published if t.endswith("/config"))

    now = datetime.now(timezone.utc)
    service = _live(db_path, lambda: now)
    _feed(service, 0.0, 81.0, now)
    values = Client()
    LiveHaPublisher(values, MQTT)(service, service.tick())
    assert values.published and not any(retain for *_, retain in values.published)


def test_nothing_is_sent_without_a_connection_and_everything_after_reconnect(db_path):
    now = datetime.now(timezone.utc)
    service = _live(db_path, lambda: now)
    _feed(service, 0.0, 81.0, now)
    client = Client()
    publisher = LiveHaPublisher(client, MQTT)
    publisher(service, service.tick())
    first = len(client.published)
    assert first > 0

    client.connected = False
    publisher(service, service.tick())
    assert len(client.published) == first

    client.connected = True
    publisher(service, service.tick())
    assert len(client.published) == 2 * first


def test_stale_measurement_becomes_unknown_never_zero(db_path):
    clock = {"now": datetime.now(timezone.utc)}
    service = _live(db_path, lambda: clock["now"])
    _feed(service, 0.0, 81.0, clock["now"])
    assert build_states(service, service.tick())["own/export_w"] == "81.0"
    clock["now"] += timedelta(seconds=LiveParams().max_age_s + 1)
    states = build_states(service, service.tick())
    assert states["own/export_w"] == "None" and states["own/import_w"] == "None" and states["own/quality"] == "stale"


def test_a_real_zero_stays_zero_and_own_value_shows_with_incomplete_balance(db_path):
    now = datetime.now(timezone.utc)
    service = _live(db_path, lambda: now)
    _feed(service, 0.0, 0.0, now)
    status = service.tick()
    states = build_states(service, status)
    assert status.status != "vollstaendig" and states["own/import_w"] == "0.0" and states["own/export_w"] == "0.0"
    assert states["leg/surplus_kw"] == "None"


# ── Overview refresh ─────────────────────────────────────────────────────────


def test_only_the_overview_refreshes_itself(db_path):
    from shareomat.web.pages import live_energy
    from shareomat.web.state import get_state

    state = get_state()
    state.register_db(db_path)
    state.register_live_service(LiveStatusService(db_path, LiveParams()))
    try:
        def page(segments):
            ctx = RequestContext(method="GET", ingress_path="", form={}, query={})
            ctx.segments = segments
            return live_energy.handle_get(ctx)

        assert 'http-equiv="refresh" content="30"' in page([])
        assert "http-equiv" not in page(["sources"]) and "http-equiv" not in page(["diagnostics"])
    finally:
        state.register_live_service(None)


# ── 7. Billing without Live-Energie ──────────────────────────────────────────


def test_billing_runs_without_live_energy_and_without_sparkplug(tmp_path, monkeypatch):
    runs = _cycle_stubs(monkeypatch)
    calls = _steps(monkeypatch)
    assert app._run_safe_cycle(_runtime(tmp_path), tmp_path / "x.db", None, None) is True
    assert len(runs) == 1 and calls == []


def test_billing_modules_do_not_import_the_live_package():
    import ast
    from pathlib import Path

    root = Path(app.__file__).resolve().parent / "shareomat"
    for folder in ("core", "database"):
        for path in (root / folder).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
            assert not any(name.startswith("shareomat.live") for name in names), path
