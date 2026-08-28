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

import logging
import os
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from shareomat.config import LegConfig, RuntimeConfig, load_runtime_config, validate_leg_config
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

logger = logging.getLogger(__name__)
_RUN_LOCK = threading.Lock()


def _maybe_recompute_demand_forecast(db_path: Path) -> None:
    """Recompute the LEG demand forecast if the last one is stale, then always publish the latest.

    Throttled independently of the settlement cycle itself: _run_safe_cycle
    can run often (watcher/cron), but weekday/time-of-day consumption
    patterns don't meaningfully change within half an hour, so recomputing
    every cycle would be wasted work — publish always reflects the most
    recently stored forecast regardless of whether this call recomputed it.
    """
    now = datetime.now(timezone.utc)
    last = latest_computed_at(db_path, scope="leg")
    if last is None or (now - last).total_seconds() >= _MIN_RECOMPUTE_INTERVAL_SECONDS:
        readings = list_meter_readings(db_path, start=now - timedelta(weeks=LOOKBACK_WEEKS), end=now)
        points = compute_leg_demand_forecast(readings, now=now, horizon_days=7, tz=_TZ)
        save_consumption_forecast(db_path, points)


def _maybe_recompute_export_price_forecast(db_path: Path) -> None:
    """Fetch/persist native-resolution ENTSO-E day-ahead prices if stale,
    throttled independently of the settlement cycle (same pattern as
    _maybe_recompute_demand_forecast above). No-op without a configured
    ENTSO-E token - graceful degradation, not an error (see
    fetch_and_persist_day_ahead_prices())."""
    token = get_external_data_settings(db_path).entsoe_api_token
    if not token:
        return
    now = datetime.now(timezone.utc)
    last = latest_fetched_at(db_path)
    if last is None or (now - last).total_seconds() >= _MIN_EXPORT_PRICE_FETCH_INTERVAL_SECONDS:
        fetch_and_persist_day_ahead_prices(db_path, api_token=token)


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
    demand_points = list_consumption_forecasts(db_path, scope="leg", start=query_start)
    near_term = [p for p in demand_points if now <= p.slot_start < now + timedelta(days=2)]
    ok_count = sum(1 for p in near_term if p.quality == "ok")
    demand_quality = "ok" if near_term and ok_count / len(near_term) >= 0.5 else "insufficient_data"
    demand_method = demand_points[0].method if demand_points else ""

    price_points = list_day_ahead_prices(db_path, start=query_start, end=now + timedelta(days=7))

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
            validate_leg_config(config)
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
            # fallback if Sparkplug is unavailable (Hard Cut).
            if sparkplug_host is not None:
                try:
                    _maybe_recompute_demand_forecast(db_path)
                    _maybe_recompute_export_price_forecast(db_path)
                    _publish_coordinator_sparkplug(sparkplug_host, config, db_path)
                except Exception as exc:
                    logger.error("Sparkplug coordinator publish failed: %s", exc)
    finally:
        _RUN_LOCK.release()


def _setup_sparkplug(runtime: RuntimeConfig) -> tuple[object | None, object | None, object | None, object | None]:
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

    sparkplug_relay = ParticipantRelay(runtime.sparkplug, runtime.mqtt)
    if not sparkplug_relay.start():
        logger.warning("Sparkplug ParticipantRelay could not connect — local Sparkplug channel disabled")
        sparkplug_relay = None

    wan_uplink = None
    wan_downlink = None
    if runtime.wan.enabled and sparkplug_relay is not None:
        wan_uplink = build_wan_uplink(runtime.wan, client_id=f"{runtime.mqtt.client_id}-wan-uplink")
        if not wan_uplink.start():
            logger.warning("WAN Uplink could not connect — Cross-House uplink disabled")
            wan_uplink = None

        wan_downlink = WanDownlink(runtime.wan, sparkplug_relay, client_id=f"{runtime.mqtt.client_id}-wan-downlink")
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
    )
    if not sparkplug_host.start():
        logger.warning("Sparkplug Host could not connect — cannot read Emsomat's local Sparkplug data")
        sparkplug_host = None

    return sparkplug_host, sparkplug_relay, wan_uplink, wan_downlink


def _stop_sparkplug(components: tuple[object | None, ...]) -> None:
    """Stop Sparkplug components in reverse-of-start order (host, then WAN, then relay)."""
    for component in components:
        if component is None:
            continue
        try:
            component.stop()
        except Exception as exc:
            logger.warning("Sparkplug component stop failed: %s", exc)


def _run_daemon(
    runtime: RuntimeConfig,
    mqtt_client: object | None,
    settings: OperationSettings,
    on_run: Callable[[], None],
    *,
    sparkplug_components: tuple[object | None, ...] = (),
) -> None:
    """Start all background daemon services and block until stopped."""
    threads = []

    if mqtt_client is not None and runtime.mqtt.command_topic_enabled:
        from shareomat.ha.mqtt_runtime import setup_command_subscription
        setup_command_subscription(mqtt_client, on_run, runtime.mqtt)

    if settings.cron_schedule:
        from shareomat.core.collector.leg_scheduler import SchedulerThread
        t = SchedulerThread(settings.cron_schedule, on_run)
        t.start()
        threads.append(t)
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
        logger.info("Share importer started: %s", runtime.paths.share_inbox)

    if runtime.email.enabled:
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
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user")
    finally:
        for t in threads:
            t.stop()
        if mqtt_client is not None:
            shutdown_mqtt(mqtt_client)
        _stop_sparkplug(sparkplug_components)


def _serve_degraded(message: str, *, port: int = 8099) -> None:
    """Start the admin web server without a working runtime config, showing the fatal error.

    Even a broken technical configuration should not leave the operator
    staring at a crashed container with no way to see why — the web UI
    still starts and surfaces the error as a persistent warning banner.
    """
    logger.error("Startup failed; serving degraded web UI: %s", message)
    web_server = WebServer(port=port)
    web_server.start()

    state = get_web_state()
    state.update(status="error", last_run="-", inbox_count=0, report_count=0, last_error=message)
    state.add_warning(
        "Shareomat konnte nicht vollständig starten, weil die technische Konfiguration "
        "fehlerhaft oder unvollständig ist. Bitte die Add-on-Konfiguration bzw. "
        "leg_config.yaml prüfen und Shareomat neu starten."
    )

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        web_server.stop()
        logger.info("Degraded web UI stopped")


def main() -> None:
    """Start the Shareomat application."""
    setup_logging()
    logger.info("shareomat starting up")

    if not _RUNTIME_CONFIG_PATH.exists():
        _serve_degraded(f"Config file not found: {_RUNTIME_CONFIG_PATH}")
        return

    try:
        runtime = load_runtime_config(_RUNTIME_CONFIG_PATH)
    except Exception as exc:
        _serve_degraded(str(exc))
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

    mqtt_client = setup_mqtt(runtime.mqtt)
    startup_warnings: list[str] = []
    if runtime.mqtt.enabled and mqtt_client is None:
        startup_warnings.append(
            "MQTT is enabled but Shareomat could not connect to the broker. "
            f"Check mqtt_host '{runtime.mqtt.broker}', port {runtime.mqtt.port}, "
            "credentials, and whether the broker is running."
        )

    sparkplug_host, sparkplug_relay, wan_uplink, wan_downlink = _setup_sparkplug(runtime)
    sparkplug_components = (sparkplug_host, wan_uplink, wan_downlink, sparkplug_relay)

    web_server = None
    if runtime.web.enabled:
        web_server = WebServer(port=runtime.web.port)
        web_server.start()
        state = get_web_state()
        state.register_db(db_path)
        state.register_runtime(runtime)
        state.register_on_run(lambda: _run_safe_cycle(runtime, db_path, mqtt_client, sparkplug_host))
        for warning in startup_warnings:
            state.add_warning(warning)

    _run_safe_cycle(runtime, db_path, mqtt_client, sparkplug_host)

    settings = get_operation_settings(db_path)
    if should_run_daemon(
        runtime.mqtt, mqtt_client,
        cron_schedule=settings.cron_schedule, auto_scan_enabled=settings.auto_scan_enabled,
    ):
        _run_daemon(
            runtime, mqtt_client, settings,
            on_run=lambda: _run_safe_cycle(runtime, db_path, mqtt_client, sparkplug_host),
            sparkplug_components=sparkplug_components,
        )
    elif web_server is not None:
        # No daemon services, but the admin web UI must keep running.
        logger.info("No daemon services configured — web UI stays up")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            web_server.stop()
            shutdown_mqtt(mqtt_client)
            _stop_sparkplug(sparkplug_components)
            logger.info("shareomat stopped")
    else:
        shutdown_mqtt(mqtt_client)
        _stop_sparkplug(sparkplug_components)
        logger.info("shareomat done")


if __name__ == "__main__":
    main()
