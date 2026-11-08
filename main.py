# -*- coding: utf-8 -*-
"""
File: main.py

Purpose:
    Entry point for the Shareomat settlement engine and admin web
    interface. Loads the technical runtime configuration, opens (and if
    needed initializes/imports) the SQLite administrative database, runs
    the settlement cycle, and optionally enters daemon mode for
    scheduled/command-triggered runs.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Master data (community, participants, meters, tariff, operating
    settings) lives in SQLite, not in the runtime YAML — see
    SHAREOMAT_UMBAU_STRUKTUR.md. A fresh installation with an empty
    database is not an error: the web UI shows a setup wizard, and
    settlement runs are skipped (not crashed) until setup is complete.

    Every settlement run rebuilds LegConfig fresh from SQLite
    (shareomat.database.config_builder.build_leg_config) immediately
    before calling shareomat.core.leg_runner.run(), so edits made in the
    admin web UI take effect on the very next run.

    Environment variables:
        SHAREOMAT_RUNTIME_CONFIG_PATH / SHAREOMAT_CONFIG_PATH
            Path to the technical runtime YAML (paths/mqtt/email/web).
            Default: config/leg_config.yaml
        SHAREOMAT_DB_PATH
            Path to the SQLite administrative database.
            Default: data/shareomat.db
        SHAREOMAT_LOG_LEVEL
            Log verbosity: DEBUG / INFO / WARNING / ERROR
"""

from __future__ import annotations

import dataclasses
import logging
import os
import signal
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from shareomat.config import LegConfig, RuntimeConfig, SparkplugConfig, WanConfig, load_runtime_config, validate_leg_config
from shareomat.core.leg_runner import run
from shareomat.core.pipeline.consumption_forecast import LOOKBACK_WEEKS, compute_leg_demand_forecast
from shareomat.core.pipeline.export_price_forecast import fetch_and_persist_day_ahead_prices
from shareomat.core.pipeline.leg_billing import resolve_producer_rate_series
from shareomat.database.config_builder import IncompleteConfigError, build_leg_config
from shareomat.database.consumption_forecasts import latest_computed_at, list_consumption_forecasts, save_consumption_forecast
from shareomat.database.day_ahead_prices import latest_fetched_at, list_day_ahead_prices
from shareomat.database.external_settings import get_external_data_settings
from shareomat.database.meter_readings import list_meter_readings
from shareomat.database.settings import OperationSettings, get_operation_settings
from shareomat.database.sqlite import init_db, resolve_db_path
from shareomat.database.yaml_import import import_legacy_yaml_if_empty
from shareomat.ha.mqtt_runtime import setup_mqtt, should_run_daemon, shutdown_mqtt
from shareomat.sparkplug import coordinator_mapping
from shareomat.sparkplug.bridge import mirror_host_to_uplink
from shareomat.sparkplug.coordinator_store import coordinator_db_path, ensure_separate_from, init_coordinator_db
from shareomat.sparkplug.host import SparkplugHost
from shareomat.sparkplug.participant_relay import ParticipantRelay
from shareomat.sparkplug.wan_downlink import WanDownlink
from shareomat.sparkplug.wan_uplink import build_wan_uplink
from shareomat.web.server import WebServer, get_state as get_web_state

_RUNTIME_CONFIG_PATH = Path(
    os.environ.get("SHAREOMAT_RUNTIME_CONFIG_PATH")
    or os.environ.get("SHAREOMAT_CONFIG_PATH")
    or "config/leg_config.yaml"
)

_TZ = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
_MIN_RECOMPUTE_INTERVAL_SECONDS = 1800  # weekday/time-of-day patterns don't change minute to minute
_MIN_EXPORT_PRICE_FETCH_INTERVAL_SECONDS = 3600  # ENTSO-E day-ahead publishes ~once/day around noon CET

# How long the coordinator tasks wait for Emsomat's NBIRTH after the first cycle of a start.
_COORDINATOR_ONLINE_WAIT_SECONDS = 30.0
# How long a shutdown waits for running coordinator tasks. Home Assistant stops an add-on after 10 s.
_COORDINATOR_STOP_TIMEOUT_SECONDS = 5.0
# Web requests and a running settlement together. With the coordinator (5 s), Sparkplug and the
# live layer this stays below the 30 s in ha_addon/config.yaml, after which Home Assistant kills the add-on.
_SHUTDOWN_DRAIN_SECONDS = 15.0

logger = logging.getLogger(__name__)
_RUN_LOCK = threading.Lock()
_COORDINATOR_LOCK = threading.Lock()
_COORDINATOR_STOP = threading.Event()
_COORDINATOR_THREAD_LOCK = threading.Lock()
_coordinator_thread: threading.Thread | None = None
_SHUTDOWN = threading.Event()
_CLEANUP_STARTED = threading.Event()
_shutdown_deadline = 0.0
_reported_communication_problem = ""


def _coordinator_store(db_path: Path) -> Path:
    """coordinator.db, created on first use. Forecasts and prices are never written to shareomat.db."""
    store = coordinator_db_path(db_path)
    ensure_separate_from(db_path, store)
    init_coordinator_db(store)
    return store


def _maybe_recompute_demand_forecast(db_path: Path) -> None:
    """Recompute the LEG demand forecast if the last one is stale, then always publish the latest.

    Throttled independently of the settlement cycle itself: _run_safe_cycle
    can run often (watcher/cron), but weekday/time-of-day consumption
    patterns don't meaningfully change within half an hour, so recomputing
    every cycle would be wasted work — publish always reflects the most
    recently stored forecast regardless of whether this call recomputed it.
    """
    store = _coordinator_store(db_path)
    now = datetime.now(timezone.utc)
    last = latest_computed_at(store, scope="leg")
    if last is None or (now - last).total_seconds() >= _MIN_RECOMPUTE_INTERVAL_SECONDS:
        readings = list_meter_readings(db_path, start=now - timedelta(weeks=LOOKBACK_WEEKS), end=now)
        points = compute_leg_demand_forecast(readings, now=now, horizon_days=7, tz=_TZ)
        save_consumption_forecast(store, points)


def _maybe_recompute_export_price_forecast(db_path: Path) -> None:
    """Fetch/persist native-resolution ENTSO-E day-ahead prices if stale,
    throttled independently of the settlement cycle (same pattern as
    _maybe_recompute_demand_forecast above). No-op without a configured
    ENTSO-E token - graceful degradation, not an error (see
    fetch_and_persist_day_ahead_prices())."""
    token = get_external_data_settings(db_path).entsoe_api_token
    if not token:
        return
    store = _coordinator_store(db_path)
    now = datetime.now(timezone.utc)
    last = latest_fetched_at(store)
    if last is None or (now - last).total_seconds() >= _MIN_EXPORT_PRICE_FETCH_INTERVAL_SECONDS:
        fetch_and_persist_day_ahead_prices(store, api_token=token)


def _publish_coordinator_sparkplug(sparkplug_host: SparkplugHost, config: LegConfig, db_path: Path) -> None:
    """Builds and sends the three Coordinator-/Market-Metrics
    (LEG/DemandForecast, LEG/ExportPrice, LEG/FeedInPrice) to the local
    Emsomat via one combined NCMD. Replaces the removed plain-MQTT
    {prefix}/energy_data/* topics (Hard Cut, see
    Emsomat_Shareomat_MQTT_Vertrag.md Abschnitt 1/20/21) - deliberately no
    MQTT fallback if this fails; Emsomat's own local logic covers gaps
    (leg.price_source handling on the Emsomat side)."""
    now = datetime.now(timezone.utc)
    ttl = timedelta(seconds=config.mqtt.energy_data_ttl_seconds)
    created_at_iso = now.isoformat()
    valid_until_iso = (now + ttl).isoformat()
    now_ms = int(now.timestamp() * 1000)

    # start liegt bewusst einen Tag VOR now, nicht bei now selbst - der
    # gerade laufende native Slot (Demand: 15min, Preis: z.B. stuendlich)
    # hat einen slot_start VOR now und muss trotzdem mitkommen, sonst
    # findet Emsomats slot_for(now) ("letzter Slot <= ts") keinen Treffer
    # fuer den aktuellen Zeitpunkt (siehe Testfund beim Bau dieser Abfrage).
    query_start = now - timedelta(days=1)
    store = _coordinator_store(db_path)
    demand_points = list_consumption_forecasts(store, scope="leg", start=query_start)
    near_term = [p for p in demand_points if now <= p.slot_start < now + timedelta(days=2)]
    ok_count = sum(1 for p in near_term if p.quality == "ok")
    demand_quality = "ok" if near_term and ok_count / len(near_term) >= 0.5 else "insufficient_data"
    demand_method = demand_points[0].method if demand_points else ""

    price_points = list_day_ahead_prices(store, start=query_start, end=now + timedelta(days=7))

    feed_in_series = resolve_producer_rate_series(
        config.tariff, config.processing, start=now, horizon_hours=48, tz=_TZ,
    )

    metrics = (
        coordinator_mapping.demand_forecast_to_metrics(
            demand_points, timestamp_ms=now_ms, created_at_iso=created_at_iso,
            valid_until_iso=valid_until_iso, quality=demand_quality, method=demand_method,
        )
        + coordinator_mapping.export_price_to_metrics(
            price_points, timestamp_ms=now_ms, created_at_iso=created_at_iso, valid_until_iso=valid_until_iso,
        )
        + coordinator_mapping.feed_in_price_to_metrics(
            feed_in_series, timestamp_ms=now_ms, created_at_iso=created_at_iso, valid_until_iso=valid_until_iso,
        )
    )
    sparkplug_host.publish_coordinator_ncmd(metrics)
    logger.info(
        "Sparkplug coordinator NCMD published (demand_slots=%d quality=%s, price_points=%d, feed_in_slots=%d)",
        len(demand_points), demand_quality, len(price_points), len(feed_in_series),
    )


def setup_logging() -> None:
    """Configure stdout logging with level from SHAREOMAT_LOG_LEVEL (default: INFO)."""
    log_level = getattr(logging, os.environ.get("SHAREOMAT_LOG_LEVEL", "INFO"), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def _update_web_state(runtime: RuntimeConfig, status: str, error: str = "") -> None:
    """Update the admin dashboard state after a settlement run attempt."""
    try:
        from datetime import datetime, timezone
        state = get_web_state()
        inbox_count = (
            sum(1 for f in runtime.paths.inbox.iterdir() if f.is_file())
            if runtime.paths.inbox.exists() else 0
        )
        report_count = (
            sum(1 for f in runtime.paths.reports.iterdir() if f.is_file())
            if runtime.paths.reports.exists() else 0
        )
        state.update(
            status=status,
            last_run=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            inbox_count=inbox_count,
            report_count=report_count,
            last_error=error,
        )
    except Exception as exc:
        logger.warning("Web state update failed: %s", exc)


def _report_communication_problem(message: str) -> None:
    """Log a communication configuration problem once and show it on the dashboard."""
    global _reported_communication_problem
    if message == _reported_communication_problem:
        return
    _reported_communication_problem = message
    logger.error("Sparkplug/WAN configuration is invalid, billing continues without it: %s", message)
    try:
        get_web_state().add_warning(f"Sparkplug-/WAN-Konfiguration ungültig, die Abrechnung läuft weiter: {message}")
    except Exception as exc:
        logger.debug("Could not publish the communication warning to the web UI: %s", exc)


def _validate_for_billing(config: LegConfig) -> None:
    """Validate a settlement configuration; only billing errors stop the run.

    The rules stay in validate_leg_config() unchanged. Sparkplug and WAN are not
    needed to settle: an invalid combination is reported, and the affected
    connection is not started (_setup_sparkplug), but billing goes on.
    """
    try:
        validate_leg_config(config)
    except ValueError as exc:
        validate_leg_config(dataclasses.replace(config, sparkplug=SparkplugConfig(), wan=WanConfig()))
        _report_communication_problem(str(exc))


def _coordinator_step(name: str, step: Callable[[], None]) -> None:
    """One coordinator step: its failure is logged and affects no other step."""
    if _COORDINATOR_STOP.is_set():
        logger.info("Sparkplug coordinator step skipped, shutting down: %s", name)
        return
    try:
        step()
    except Exception:
        logger.exception("Sparkplug coordinator step failed: %s", name)


def _publish_coordinator_if_running(sparkplug_host: SparkplugHost, config: LegConfig, db_path: Path) -> None:
    """The check here ends the normal case; SparkplugHost itself closes the race with its stop()."""
    if _COORDINATOR_STOP.is_set():
        logger.info("LEG data for Emsomat not sent, shutting down")
        return
    _publish_coordinator_sparkplug(sparkplug_host, config, db_path)


def _run_coordinator_tasks(sparkplug_host: SparkplugHost, config: LegConfig, db_path: Path) -> None:
    """Forecast, price fetch and LEG data for Emsomat (automatic, only with Emsomat online).

    Runs outside _RUN_LOCK: a slow price server or a failing forecast can neither
    delay nor block a settlement run. Forecast and prices are only ever used for
    this message, so without an Emsomat session there is nothing to do.
    """
    if not _COORDINATOR_LOCK.acquire(blocking=False):
        logger.debug("Sparkplug coordinator tasks already running, skipped")
        return
    try:
        deadline = time.monotonic() + _COORDINATOR_ONLINE_WAIT_SECONDS
        while not sparkplug_host.emsomat_online and time.monotonic() < deadline:
            if _COORDINATOR_STOP.wait(0.2):
                return
        if not sparkplug_host.emsomat_online:
            logger.info("Emsomat is not online: no forecast, price fetch or LEG data in this cycle")
            return
        _coordinator_step("demand forecast", lambda: _maybe_recompute_demand_forecast(db_path))
        _coordinator_step("export price forecast", lambda: _maybe_recompute_export_price_forecast(db_path))
        _coordinator_step("LEG data to Emsomat", lambda: _publish_coordinator_if_running(sparkplug_host, config, db_path))
    finally:
        _COORDINATOR_LOCK.release()


def _start_coordinator_tasks(sparkplug_host: SparkplugHost, config: LegConfig, db_path: Path) -> None:
    """At most one worker; none once a shutdown has begun. Daemon, so that a
    request that cannot be aborted can never keep the process alive."""
    global _coordinator_thread
    with _COORDINATOR_THREAD_LOCK:
        if _COORDINATOR_STOP.is_set() or _SHUTDOWN.is_set():
            logger.info("Sparkplug coordinator tasks not started, shutting down")
            return
        if _coordinator_thread is not None and _coordinator_thread.is_alive():
            logger.debug("Sparkplug coordinator tasks still running, not started again")
            return
        try:
            thread = threading.Thread(
                target=_run_coordinator_tasks, args=(sparkplug_host, config, db_path),
                name="shareomat-coordinator", daemon=True,
            )
            thread.start()
        except Exception:
            logger.exception("Sparkplug coordinator tasks could not be started")
            return
        _coordinator_thread = thread


def _stop_coordinator() -> None:
    """Stop the coordinator before the Sparkplug clients go away.

    A running forecast or HTTP request cannot be interrupted. After the
    timeout it is left behind: it can still finish writing to coordinator.db,
    but SparkplugHost refuses anything it tries to send once it is stopped.
    """
    with _COORDINATOR_THREAD_LOCK:
        _COORDINATOR_STOP.set()
        thread = _coordinator_thread
    if thread is None or not thread.is_alive():
        return
    logger.info("Waiting for the Sparkplug coordinator tasks to finish")
    thread.join(_COORDINATOR_STOP_TIMEOUT_SECONDS)
    if thread.is_alive():
        logger.warning(
            "Sparkplug coordinator task still running after %.0f s (forecast or price request cannot be "
            "aborted); left behind, it sends nothing more", _COORDINATOR_STOP_TIMEOUT_SECONDS,
        )


def _run_safe_cycle(
    runtime: RuntimeConfig, db_path: Path, mqtt_client: object | None,
    sparkplug_host: SparkplugHost | None = None,
) -> bool:
    """Build a fresh LegConfig from SQLite and run one settlement cycle, unless one is active.

    Every trigger path (startup, MQTT command, cron, file watcher, share
    importer, and the web UI) goes through this function, so the lock
    protects the inbox, archive, reports, and processed-file state from
    concurrent settlement runs.
    """
    if _SHUTDOWN.is_set():
        logger.info("Settlement run skipped: shutting down")
        return False
    if not _RUN_LOCK.acquire(blocking=False):
        logger.warning("Settlement run skipped: another run is already active")
        try:
            get_web_state().set_flash(
                "Lauf übersprungen: Es läuft bereits ein Abrechnungslauf.", ok=False,
            )
        except Exception as exc:
            logger.debug("Could not publish skipped-run notice to web UI: %s", exc)
        return False

    try:
        try:
            config = build_leg_config(db_path, runtime)
            _validate_for_billing(config)
        except IncompleteConfigError as exc:
            logger.info("Settlement run skipped: %s", exc)
            _update_web_state(runtime, "starting", str(exc))
            return False
        except ValueError as exc:
            logger.error("Settlement run skipped: invalid configuration: %s", exc)
            _update_web_state(runtime, "error", str(exc))
            return False

        try:
            run(config, mqtt_client=mqtt_client, db_path=db_path)
            _update_web_state(runtime, "ok", "")
            return True
        except Exception as exc:
            logger.error("Settlement run failed: %s", exc)
            if mqtt_client is not None:
                from shareomat.ha.mqtt_runtime import publish_status
                publish_status(mqtt_client, "error", runtime.mqtt)
            _update_web_state(runtime, "error", str(exc))
            return True
        finally:
            # Published on every cycle, independent of run() outcome, so
            # Coordinator-/Market-data stays fresh on the normal cron
            # cadence even on cycles where the inbox had nothing new. Only
            # the local Sparkplug channel (ADR-0004) - no plain-MQTT
            # fallback if Sparkplug is unavailable (Hard Cut). The work itself
            # runs in the background, outside the settlement lock.
            if sparkplug_host is not None:
                _start_coordinator_tasks(sparkplug_host, config, db_path)
    finally:
        _RUN_LOCK.release()


def _setup_live(db_path: Path, runtime: RuntimeConfig, mqtt_client: object | None) -> object | None:
    """Start the live layer (ADR-0007). Any failure, including a defect in the
    package itself, leaves Live-Energie unavailable and everything else running."""
    try:
        from shareomat.ha.live_sensors import LiveHaPublisher, publish_live_discovery
        from shareomat.live.service import LiveStatusService

        service = LiveStatusService(db_path)
        if mqtt_client is not None:
            publish_live_discovery(mqtt_client, runtime.mqtt)
            service.add_listener(LiveHaPublisher(mqtt_client, runtime.mqtt))
        service.start()
    except Exception:
        logger.exception("Live-Energie is not available; administration and billing continue without it")
        return None
    return service


def _live_adapters() -> tuple[type, type, str] | None:
    """(local adapter, WAN adapter, local adapter name), or None if they cannot be loaded."""
    try:
        from shareomat.live.adapters.sparkplug import EmsomatLocalAdapter, WanSparkplugAdapter
        from shareomat.models.live_source import ADAPTER_EMSOMAT_LOCAL
    except Exception:
        logger.exception("Live adapters could not be loaded; Sparkplug runs without Live-Energie")
        return None
    return EmsomatLocalAdapter, WanSparkplugAdapter, ADAPTER_EMSOMAT_LOCAL


def _wan_config_gaps(wan: WanConfig) -> list[str]:
    fields = {"wan.broker": wan.broker, "wan.group_id": wan.group_id,
              "wan.edge_node_id": wan.edge_node_id, "wan.own_participant_id": wan.own_participant_id}
    return [name for name, value in fields.items() if not value.strip()]


def _log_automatic_functions(runtime: RuntimeConfig, db_path: Path, sparkplug_host: object | None) -> None:
    """State plainly what runs by itself, so that nothing is switched on unnoticed."""
    if sparkplug_host is None:
        logger.info("Automatic functions: Sparkplug is not active, no LEG data to Emsomat, no forecast, no price fetch")
        return
    try:
        price_fetch = "active (ENTSO-E/SNB)" if get_external_data_settings(db_path).entsoe_api_token else "off (no ENTSO-E token)"
    except Exception:
        price_fetch = "unknown"
    logger.info(
        "Automatic functions: Sparkplug receives Emsomat node %s; while Emsomat is online each settlement "
        "cycle also computes the demand forecast, sends LEG data to Emsomat; price fetch: %s",
        runtime.sparkplug.emsomat_edge_node_id, price_fetch,
    )


def _setup_sparkplug(
    runtime: RuntimeConfig, live_service: object | None = None,
) -> tuple[object | None, object | None, object | None, object | None]:
    """Start the Sparkplug B local channel (Emsomat<->Shareomat) and, if configured, the
    Cross-House WAN channel (Shareomat<->Shareomat). Returns
    (sparkplug_host, sparkplug_relay, wan_uplink, wan_downlink) - any of which is None
    if disabled or its connection failed (failure here never blocks the settlement engine
    or the plain MQTT/HA-discovery channel, see docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md).

    Started once at process startup (like the plain mqtt_client), not per settlement cycle -
    these are long-lived Sparkplug sessions, not settlement-run outputs.
    """
    if not runtime.sparkplug.enabled:
        return None, None, None, None
    if not runtime.sparkplug.emsomat_edge_node_id.strip():
        _report_communication_problem("sparkplug.emsomat_edge_node_id is empty - Sparkplug is not started")
        return None, None, None, None

    adapters = _live_adapters() if live_service is not None else None
    if adapters is None:
        live_service = None
    local_adapter, wan_adapter, local_adapter_name = adapters or (None, None, "")

    sparkplug_relay = ParticipantRelay(runtime.sparkplug, runtime.mqtt)
    if not sparkplug_relay.start():
        logger.warning("Sparkplug ParticipantRelay could not connect — local Sparkplug channel disabled")
        sparkplug_relay = None

    wan_uplink = None
    wan_downlink = None
    wan_gaps = _wan_config_gaps(runtime.wan) if runtime.wan.enabled else []
    if wan_gaps:
        _report_communication_problem(f"missing {', '.join(wan_gaps)} - the WAN connection is not started")
    if runtime.wan.enabled and not wan_gaps and sparkplug_relay is not None:
        wan_uplink = build_wan_uplink(runtime.wan, client_id=f"{runtime.mqtt.client_id}-wan-uplink")
        if not wan_uplink.start():
            logger.warning("WAN Uplink could not connect — Cross-House uplink disabled")
            wan_uplink = None

        # Only sources registered in "Live-Energie" are accepted. Without the live layer: none.
        wan_downlink = WanDownlink(
            runtime.wan, sparkplug_relay, client_id=f"{runtime.mqtt.client_id}-wan-downlink",
            allowed_source=live_service.is_registered_wan if live_service is not None else (lambda edge, device: False),
            live_sink=wan_adapter(live_service) if live_service is not None else None,
        )
        if live_service is not None:
            live_service.register_unknown_pairs_getter(lambda: wan_downlink.unknown_pairs if wan_downlink else ())
        if not wan_downlink.start():
            logger.warning("WAN Downlink could not connect — Cross-House downlink disabled")
            wan_downlink = None

    def _on_emsomat_metrics_changed() -> None:
        if wan_uplink is None:
            return
        mirror_host_to_uplink(sparkplug_host, wan_uplink, runtime.wan.own_participant_id)

    sparkplug_host = SparkplugHost(
        runtime.sparkplug, runtime.mqtt,
        on_metrics_changed=_on_emsomat_metrics_changed if wan_uplink is not None else None,
        live_sink=local_adapter(live_service, runtime.sparkplug.emsomat_edge_node_id) if live_service is not None else None,
    )
    if live_service is not None:
        live_service.register_rebirth_handler(local_adapter_name, sparkplug_host.request_rebirth)
    if not sparkplug_host.start():
        logger.warning("Sparkplug Host could not connect — cannot read Emsomat's local Sparkplug data")
        sparkplug_host = None

    return sparkplug_host, sparkplug_relay, wan_uplink, wan_downlink


def _stop_sparkplug(components: tuple[object | None, ...]) -> None:
    """Stop the coordinator, then the Sparkplug components (host, then WAN, then relay)."""
    _stop_coordinator()
    for component in components:
        if component is None:
            continue
        try:
            component.stop()
        except Exception as exc:
            logger.warning("Sparkplug component stop failed: %s", exc)


class _ShutdownSequence:
    """Stops what was actually started, exactly once and newest first.

    Start order gives the right stop order: daemon threads, web server, running
    settlement, coordinator and Sparkplug, live layer, MQTT. A failing step does
    not keep the others from running. Whatever is still alive at the end is
    logged; the process then exits, and SQLite rolls back unfinished transactions.
    """

    def __init__(self) -> None:
        self._steps: list[tuple[str, Callable[[], None]]] = []
        self._lock = threading.Lock()
        self._ran = False

    def add(self, name: str, stop: Callable[[], None]) -> None:
        self._steps.append((name, stop))

    def run(self) -> None:
        global _shutdown_deadline
        with self._lock:
            if self._ran:
                return
            self._ran = True
        _shutdown_deadline = time.monotonic() + _SHUTDOWN_DRAIN_SECONDS
        _SHUTDOWN.set()
        _CLEANUP_STARTED.set()
        _COORDINATOR_STOP.set()
        for name, stop in reversed(self._steps):
            try:
                stop()
            except Exception:
                logger.exception("Shutdown step failed: %s", name)
        others = [t.name for t in threading.enumerate() if t is not threading.current_thread() and t.is_alive()]
        logger.info("shareomat stopped; threads still alive at exit: %s", ", ".join(others) or "none")


def _drain_seconds_left() -> float:
    return max(0.0, _shutdown_deadline - time.monotonic())


def _wait_for_running_settlement() -> None:
    """A cycle started by the scheduler, the watcher or the web UI may finish, within the deadline."""
    if not _RUN_LOCK.acquire(timeout=_drain_seconds_left()):
        logger.warning("A settlement run is still active at the end of the shutdown deadline")
        return
    _RUN_LOCK.release()


def _request_shutdown(signum, frame) -> None:
    """SIGTERM and Ctrl+C ask for the shutdown; running work is not torn apart.

    A second signal ends a start that is stuck. During the cleanup itself it is ignored.
    """
    if not _SHUTDOWN.is_set():
        logger.info("Signal %s received, shutting down", signum)
        _SHUTDOWN.set()
    elif not _CLEANUP_STARTED.is_set():
        raise KeyboardInterrupt


def _install_signal_handlers() -> None:
    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)


def _wait_for_shutdown() -> None:
    while not _SHUTDOWN.wait(1):
        pass


def _run_daemon(
    runtime: RuntimeConfig,
    mqtt_client: object | None,
    settings: OperationSettings,
    on_run: Callable[[], None],
    *,
    shutdown: _ShutdownSequence,
) -> None:
    """Start all background daemon services and block until a shutdown is requested."""
    threads = []

    if mqtt_client is not None and runtime.mqtt.command_topic_enabled:
        from shareomat.ha.mqtt_runtime import setup_command_subscription
        setup_command_subscription(mqtt_client, on_run, runtime.mqtt)

    if settings.cron_schedule:
        from shareomat.core.collector.leg_scheduler import SchedulerThread
        t = SchedulerThread(settings.cron_schedule, on_run)
        t.start()
        threads.append(t)
        shutdown.add("cron scheduler", t.stop)
        logger.info("Cron scheduler started: %s", settings.cron_schedule)

    if runtime.paths.share_inbox:
        from shareomat.core.collector.leg_share_importer import ShareImporterThread
        share_t = ShareImporterThread(
            share_path=Path(runtime.paths.share_inbox),
            inbox_path=runtime.paths.inbox,
            interval=settings.scan_interval_seconds,
        )
        share_t.start()
        threads.append(share_t)
        shutdown.add("share importer", share_t.stop)
        logger.info("Share importer started: %s", runtime.paths.share_inbox)

    if runtime.email.enabled:
        get_web_state().add_warning(
            "E-Mail-Import: Der From-Filter authentifiziert keine Absender. "
            "Der empfangende Mailserver muss vertrauenswürdige Zustellung gewährleisten."
            + (" Die leere Absenderliste erlaubt Nachrichten von allen Absendern."
               if not any(address.strip() for address in runtime.email.allowed_senders) else "")
        )
        from shareomat.core.collector.leg_email_importer import EmailImporterThread
        email_t = EmailImporterThread(
            imap_host=runtime.email.imap_host,
            imap_port=runtime.email.imap_port,
            username=runtime.email.username,
            password=runtime.email.password,
            folder=runtime.email.folder,
            allowed_senders=runtime.email.allowed_senders,
            inbox_path=runtime.paths.inbox,
            state_dir=runtime.paths.state,
            interval=runtime.email.poll_interval_seconds,
        )
        email_t.start()
        threads.append(email_t)
        shutdown.add("email importer", email_t.stop)
        logger.info("Email importer started: %s@%s", runtime.email.username, runtime.email.imap_host)

    watcher = None
    if settings.auto_scan_enabled:
        from shareomat.core.collector.leg_watcher import WatcherThread
        watcher = WatcherThread(
            runtime.paths.inbox,
            on_run,
            interval=settings.scan_interval_seconds,
        )
        watcher.start()
        threads.append(watcher)
        shutdown.add("file watcher", watcher.stop)
        if mqtt_client is not None:
            from shareomat.ha.mqtt_runtime import register_watcher
            register_watcher(watcher)
        if mqtt_client is not None:
            prefix = runtime.mqtt.topic_prefix
            mqtt_client.publish(f"{prefix}/auto_scan/state", "ON", qos=runtime.mqtt.qos, retain=True)

    service_count = len(threads)
    if mqtt_client is not None and runtime.mqtt.command_topic_enabled:
        service_count += 1
    if service_count == 0:
        logger.warning("Daemon mode requested but no services configured — exiting")
        return

    logger.info("Daemon mode active — %d service(s) running", service_count)
    _wait_for_shutdown()


def _prepare_login(db_path, runtime) -> None:
    """No account yet (or a reset was requested): write a fresh one-time setup code to the log."""
    from shareomat.database.auth import issue_setup_code, request_login_reset
    if runtime.web.reset_login:
        request_login_reset(db_path)
    issue_setup_code(db_path)


def _serve_degraded(message: str, *, port: int = 8099, shutdown: _ShutdownSequence) -> None:
    """Start the admin web server without a working runtime config, showing the fatal error.

    Even a broken technical configuration should not leave the operator
    staring at a crashed container with no way to see why — the web UI
    still starts and surfaces the error as a persistent warning banner.
    """
    logger.error("Startup failed; serving degraded web UI: %s", message)
    web_server = WebServer(port=port)
    web_server.start()
    shutdown.add("web server", lambda: web_server.stop(timeout=_drain_seconds_left()))

    state = get_web_state()
    state.update(status="error", last_run="-", inbox_count=0, report_count=0, last_error=message)
    state.add_warning(
        "Shareomat konnte nicht vollständig starten, weil die technische Konfiguration "
        "fehlerhaft oder unvollständig ist. Bitte die Add-on-Konfiguration bzw. "
        "leg_config.yaml prüfen und Shareomat neu starten."
    )
    _wait_for_shutdown()


def _start_and_serve(shutdown: _ShutdownSequence) -> None:
    """Start the components one by one. Each is registered for the shutdown as soon as it runs."""
    if not _RUNTIME_CONFIG_PATH.exists():
        _serve_degraded(f"Config file not found: {_RUNTIME_CONFIG_PATH}", shutdown=shutdown)
        return

    try:
        runtime = load_runtime_config(_RUNTIME_CONFIG_PATH)
    except Exception as exc:
        _serve_degraded(str(exc), shutdown=shutdown)
        return

    for directory in (runtime.paths.inbox, runtime.paths.archive, runtime.paths.reports, runtime.paths.state):
        directory.mkdir(parents=True, exist_ok=True)

    db_path = resolve_db_path()
    init_db(db_path)
    try:
        if import_legacy_yaml_if_empty(db_path, _RUNTIME_CONFIG_PATH):
            logger.info("Legacy configuration imported into %s", db_path)
    except Exception as exc:
        logger.error("Legacy YAML import failed (continuing with an empty database): %s", exc)
    if _SHUTDOWN.is_set():
        return

    mqtt_client = setup_mqtt(runtime.mqtt)
    if mqtt_client is not None:
        shutdown.add("MQTT", lambda: shutdown_mqtt(mqtt_client))
    startup_warnings: list[str] = []
    if runtime.mqtt.enabled and mqtt_client is None:
        startup_warnings.append(
            "MQTT is enabled but Shareomat could not connect to the broker. "
            f"Check mqtt_host '{runtime.mqtt.broker}', port {runtime.mqtt.port}, "
            "credentials, and whether the broker is running."
        )
    if _SHUTDOWN.is_set():
        return

    live_service = _setup_live(db_path, runtime, mqtt_client)
    if live_service is None:
        startup_warnings.append("Live-Energie ist nicht verfügbar. Details stehen im Add-on-Log. Verwaltung und Abrechnung laufen weiter.")
    else:
        shutdown.add("live layer", live_service.stop)
    if _SHUTDOWN.is_set():
        return

    sparkplug_host, sparkplug_relay, wan_uplink, wan_downlink = _setup_sparkplug(runtime, live_service)
    sparkplug_components = tuple(c for c in (sparkplug_host, wan_uplink, wan_downlink, sparkplug_relay) if c is not None)
    if sparkplug_components:
        shutdown.add("coordinator and Sparkplug", lambda: _stop_sparkplug(sparkplug_components))
    _log_automatic_functions(runtime, db_path, sparkplug_host)
    shutdown.add("running settlement", _wait_for_running_settlement)
    if _SHUTDOWN.is_set():
        return

    web_server = None
    if runtime.web.enabled:
        web_server = WebServer(port=runtime.web.port)
        web_server.start()
        shutdown.add("web server", lambda: web_server.stop(timeout=_drain_seconds_left()))
        state = get_web_state()
        state.register_db(db_path)
        state.register_runtime(runtime)
        state.register_live_service(live_service)
        _prepare_login(db_path, runtime)
        state.register_on_run(lambda: _run_safe_cycle(runtime, db_path, mqtt_client, sparkplug_host))
        if sparkplug_host is not None:
            state.register_emsomat_online(lambda: sparkplug_host.emsomat_online)
        for warning in startup_warnings:
            state.add_warning(warning)
    if _SHUTDOWN.is_set():
        return

    # A signal during this cycle only sets the flag: the cycle ends normally, then the shutdown runs.
    _run_safe_cycle(runtime, db_path, mqtt_client, sparkplug_host)
    if _SHUTDOWN.is_set():
        return

    settings = get_operation_settings(db_path)
    if should_run_daemon(
        runtime.mqtt, mqtt_client,
        cron_schedule=settings.cron_schedule, auto_scan_enabled=settings.auto_scan_enabled,
    ):
        _run_daemon(
            runtime, mqtt_client, settings,
            on_run=lambda: _run_safe_cycle(runtime, db_path, mqtt_client, sparkplug_host),
            shutdown=shutdown,
        )
    elif web_server is not None:
        # No daemon services, but the admin web UI must keep running.
        logger.info("No daemon services configured — web UI stays up")
        _wait_for_shutdown()


def main() -> None:
    """Start the Shareomat application and always leave through the same shutdown."""
    setup_logging()
    logger.info("shareomat starting up")
    _install_signal_handlers()
    shutdown = _ShutdownSequence()
    try:
        _start_and_serve(shutdown)
    except KeyboardInterrupt:
        logger.info("Start interrupted")
    finally:
        shutdown.run()


if __name__ == "__main__":
    main()
