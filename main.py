# -*- coding: utf-8 -*-
"""
File: main.py

Purpose:
    Entry point for the SELF LEG settlement engine.
    Configures logging, sets up optional MQTT, runs the settlement
    cycle, and optionally enters daemon mode for command-triggered runs.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    Daemon mode activates automatically when mqtt.enabled = true
    and mqtt.command_topic_enabled = true in leg_config.yaml, OR when
    cron_schedule or auto_scan_enabled is set in processing config.
    In daemon mode the container stays alive and listens on
    self_leg/cmd/run_once for manual trigger messages.
    Without MQTT or with command_topic_enabled = false the process
    runs once and exits — identical to the original behaviour.

    Environment variables (set by the HA add-on run.sh, optional otherwise):
        SELF_LEG_CONFIG_PATH  Override default config path (config/leg_config.yaml)
        SELF_LEG_LOG_LEVEL    Log verbosity: DEBUG / INFO / WARNING / ERROR
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from self_leg.ha.mqtt_runtime import setup_mqtt, should_run_daemon, shutdown_mqtt
from self_leg.ha.ingress import IngressServer, get_state as get_ingress_state
from self_leg.core.leg_config import LegConfig, load_config
from self_leg.core.leg_runner import run

_CONFIG_PATH = Path(os.environ.get("SELF_LEG_CONFIG_PATH", "config/leg_config.yaml"))

logger = logging.getLogger(__name__)
_RUN_LOCK = threading.Lock()
_ERROR_DASHBOARD_ENABLED = os.environ.get("SELF_LEG_ERROR_DASHBOARD", "").lower() in {
    "1", "true", "yes", "on",
}


def setup_logging() -> None:
    """Configure stdout logging with level from SELF_LEG_LOG_LEVEL (default: INFO)."""
    log_level = getattr(logging, os.environ.get("SELF_LEG_LOG_LEVEL", "INFO"), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


def _update_ingress_state(config: LegConfig, error: str = "") -> None:
    """Update the ingress dashboard state after a settlement run."""
    try:
        from datetime import datetime, timezone
        state = get_ingress_state()
        inbox_count = (
            sum(1 for f in config.paths.inbox.iterdir() if f.is_file())
            if config.paths.inbox.exists() else 0
        )
        report_count = (
            sum(1 for f in config.paths.reports.iterdir() if f.is_file())
            if config.paths.reports.exists() else 0
        )
        state.update(
            status="error" if error else "ok",
            last_run=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            inbox_count=inbox_count,
            report_count=report_count,
            last_error=error,
        )
    except Exception as exc:
        logger.warning("Ingress state update failed: %s", exc)


def _run_safe_cycle(config_path: Path, config: LegConfig, mqtt_client: object | None) -> bool:
    """Run one settlement cycle unless another cycle is already active.

    Every trigger path (startup, MQTT command, cron, file watcher, share importer,
    and Ingress) goes through this function, so the lock protects the inbox,
    archive, reports, and processed-file state from concurrent settlement runs.
    """
    if not _RUN_LOCK.acquire(blocking=False):
        logger.warning("Settlement run skipped: another run is already active")
        try:
            get_ingress_state().set_upload_result(
                "Run skipped: another settlement run is already active.",
                ok=False,
            )
        except Exception as exc:
            logger.debug("Could not publish skipped-run notice to Ingress: %s", exc)
        return False

    try:
        run(config_path, mqtt_client=mqtt_client)
        _update_ingress_state(config, error="")
        return True
    except Exception as exc:
        logger.error("Settlement run failed: %s", exc)
        if mqtt_client is not None:
            from self_leg.ha.mqtt_runtime import publish_status
            publish_status(mqtt_client, "error", config.mqtt)
        _update_ingress_state(config, error=str(exc))
        return True
    finally:
        _RUN_LOCK.release()


def _run_daemon(
    config: LegConfig,
    mqtt_client: object | None,
    on_run: Callable[[], None],
) -> None:
    """Start all background daemon services and block until stopped."""
    import time
    threads = []

    # MQTT command subscription (non-blocking setup)
    if mqtt_client is not None and config.mqtt.command_topic_enabled:
        from self_leg.ha.mqtt_runtime import setup_command_subscription
        setup_command_subscription(mqtt_client, on_run, config.mqtt)

    # Cron scheduler
    if config.processing.cron_schedule:
        from self_leg.core.collector.leg_scheduler import SchedulerThread
        t = SchedulerThread(config.processing.cron_schedule, on_run)
        t.start()
        threads.append(t)
        logger.info("Cron scheduler started: %s", config.processing.cron_schedule)

    # Share folder importer
    if config.paths.share_inbox:
        from self_leg.core.collector.leg_share_importer import ShareImporterThread
        share_t = ShareImporterThread(
            share_path=Path(config.paths.share_inbox),
            inbox_path=config.paths.inbox,
            interval=config.processing.scan_interval_seconds,
        )
        share_t.start()
        threads.append(share_t)
        logger.info("Share importer started: %s", config.paths.share_inbox)

    # Email importer (IMAP mailbox, e.g. a dedicated Gmail inbox)
    if config.email.enabled:
        from self_leg.core.collector.leg_email_importer import EmailImporterThread
        email_t = EmailImporterThread(
            imap_host=config.email.imap_host,
            imap_port=config.email.imap_port,
            username=config.email.username,
            password=config.email.password,
            folder=config.email.folder,
            allowed_senders=config.email.allowed_senders,
            inbox_path=config.paths.inbox,
            state_dir=config.paths.state,
            interval=config.email.poll_interval_seconds,
        )
        email_t.start()
        threads.append(email_t)
        logger.info("Email importer started: %s@%s", config.email.username, config.email.imap_host)

    # File watcher
    watcher = None
    if config.processing.auto_scan_enabled:
        from self_leg.core.collector.leg_watcher import WatcherThread
        watcher = WatcherThread(
            config.paths.inbox,
            on_run,
            interval=config.processing.scan_interval_seconds,
        )
        watcher.start()
        threads.append(watcher)
        # Wire watcher to MQTT runtime for switch control
        if mqtt_client is not None:
            from self_leg.ha.mqtt_runtime import register_watcher
            register_watcher(watcher)
        # Publish initial auto_scan state
        if mqtt_client is not None:
            prefix = config.mqtt.topic_prefix
            mqtt_client.publish(f"{prefix}/auto_scan/state", "ON", qos=config.mqtt.qos, retain=True)

    service_count = len(threads)
    if mqtt_client is not None and config.mqtt.command_topic_enabled:
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
            from self_leg.ha.mqtt_runtime import shutdown_mqtt as _shutdown
            _shutdown(mqtt_client)


def _serve_startup_error(message: str, *, port: int = 8099) -> None:
    """Start a minimal Ingress dashboard that explains why startup stopped."""
    if not _ERROR_DASHBOARD_ENABLED:
        logger.error("%s", message)
        sys.exit(1)

    logger.error("Startup failed; serving Ingress error dashboard: %s", message)
    ingress_server = IngressServer(port=port)
    ingress_server.start()

    state = get_ingress_state()
    state.update(
        status="error",
        last_run="-",
        inbox_count=0,
        report_count=0,
        last_error=message,
    )
    state.add_warning(
        "SELF LEG could not start because the configuration is incomplete or invalid. "
        "Open the add-on Configuration tab, fix the items below, then restart the add-on."
    )
    state.register_inbox(Path("/config/self_leg/inbox"))
    state.register_reports(Path("/config/self_leg/reports"))
    state.register_meters([])

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        ingress_server.stop()
        logger.info("Startup error dashboard stopped")


def main() -> None:
    """Start the SELF LEG application."""
    setup_logging()
    logger.info("self_leg starting up")

    if not _CONFIG_PATH.exists():
        _serve_startup_error(f"Config file not found: {_CONFIG_PATH}")
        return

    try:
        config = load_config(_CONFIG_PATH)
    except Exception as exc:
        _serve_startup_error(str(exc))
        return
    mqtt_client = setup_mqtt(config)
    startup_warnings: list[str] = []
    if config.mqtt.enabled and mqtt_client is None:
        startup_warnings.append(
            "MQTT is enabled but SELF LEG could not connect to the broker. "
            f"Check mqtt_host '{config.mqtt.broker}', port {config.mqtt.port}, "
            "credentials, and whether the broker is running."
        )

    # Start ingress server if enabled
    ingress_server = None
    if config.ingress.enabled:
        ingress_server = IngressServer(port=config.ingress.port)
        ingress_server.start()
        _ingress = get_ingress_state()
        _ingress.register_on_run(lambda: _run_safe_cycle(_CONFIG_PATH, config, mqtt_client))
        _ingress.register_inbox(config.paths.inbox)
        _ingress.register_reports(config.paths.reports)
        _ingress.register_meters([(m.meter_id, m.label) for m in config.meters])
        for warning in startup_warnings:
            _ingress.add_warning(warning)

    _run_safe_cycle(_CONFIG_PATH, config, mqtt_client)

    if should_run_daemon(config, mqtt_client):
        _run_daemon(
            config, mqtt_client,
            on_run=lambda: _run_safe_cycle(_CONFIG_PATH, config, mqtt_client),
        )
    else:
        shutdown_mqtt(mqtt_client)
        logger.info("self_leg done")


if __name__ == "__main__":
    main()
