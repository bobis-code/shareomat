# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_runner.py

Purpose:
    Pipeline orchestration for a complete Shareomat settlement cycle.
    Coordinates scan, parse, match, billing, report generation,
    state persistence, and archiving in the correct order.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Processing order is strict:
      1. Parse
      2. Filter unknown/inactive meters (unknown_meter_policy)
      3. Match
      4. Billing
      5. Write reports
      6. Verify reports exist on disk
      7. Mark files as processed (with report file references)
      8. Archive source files
      9. Publish via MQTT (if client provided)
    Files are never archived before reports are safely written.
    MQTT publish failure does not roll back the settlement cycle.

    run() takes an already-built LegConfig rather than a path — the
    caller (main.py) builds a fresh one from SQLite before every run via
    shareomat.database.config_builder.build_leg_config(), so edits made
    in the admin web UI take effect on the very next run.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shareomat.core.pipeline.leg_billing import compute_billing
from shareomat.config import LegConfig
from shareomat.leg_const import UNKNOWN_METER_POLICY_FAIL
from shareomat.core.collector.leg_import import move_to_archive, scan_inbox
from shareomat.core.pipeline.leg_matcher import match_all
from shareomat.models.meter_data import ImportFile, IntervalReading
from shareomat.core.pipeline.leg_parser import parse_csv, parse_sdat, readings_to_slots
from shareomat.core.report.leg_report import (
    write_billing_csv,
    write_billing_json,
    write_community_audit_csv,
    write_community_summary_json,
    write_ledger_csv,
    write_match_csv,
)
from shareomat.core.collector.leg_storage import is_processed, mark_processed
from shareomat.database.meter_readings import save_meter_readings
from shareomat.database.settlement_history import save_settlement_snapshot

logger = logging.getLogger(__name__)


def parse_file(
    imp: ImportFile,
    slot_minutes: int,
    known_meter_ids: set[str] | None,
) -> list[IntervalReading]:
    """Parse one file into meter readings, choosing the right parser by type.

    Public (not prefixed `_`) because shareomat.database.billing reuses it
    to parse inbox/archive files on demand for an arbitrary billing period,
    outside the automatic scan-new-files-only cycle below.
    """
    if imp.file_type == "csv":
        return parse_csv(imp.path, slot_minutes=slot_minutes, known_meter_ids=known_meter_ids)
    if imp.file_type == "sdat":
        return parse_sdat(imp.path, slot_minutes=slot_minutes, known_meter_ids=known_meter_ids)
    if imp.file_type == "xlsx":
        from shareomat.core.pipeline.raw.ebl_xlsx import parse as parse_ebl_xlsx
        return parse_ebl_xlsx(imp.path, slot_minutes=slot_minutes, known_meter_ids=known_meter_ids)
    raise ValueError(f"Unknown file type: {imp.file_type}")


def _filter_readings(
    readings: list[IntervalReading],
    known_meter_ids: set[str],
    policy: str,
) -> list[IntervalReading]:
    """Remove readings from unknown meters; raise or warn depending on policy."""
    unknown = {r.meter_id for r in readings if r.meter_id not in known_meter_ids}
    if unknown:
        msg = f"readings from unknown meter IDs: {sorted(unknown)}"
        if policy == UNKNOWN_METER_POLICY_FAIL:
            raise ValueError(f"Unknown meter IDs in input — {msg}")
        logger.warning("Skipping %s", msg)
    return [r for r in readings if r.meter_id in known_meter_ids]


def run(config: LegConfig, mqtt_client: object = None, db_path: Path | None = None) -> None:
    """Execute a full settlement cycle: scan → parse → match → bill → report → archive."""
    logger.info(
        "Settlement run started — %s (%s)", config.community.name, config.community.community_id
    )

    inbox = config.paths.inbox
    archive = config.paths.archive
    reports = config.paths.reports
    state_dir = config.paths.state
    slot_minutes = config.processing.slot_minutes
    known_meter_ids = {m.meter_id for m in config.meters if m.active}

    for directory in (inbox, archive, reports, state_dir):
        directory.mkdir(parents=True, exist_ok=True)

    import_files = scan_inbox(inbox)
    if not import_files:
        logger.info("Inbox is empty — nothing to process.")
        return

    all_readings: list[IntervalReading] = []
    newly_processed: list[ImportFile] = []

    for imp in import_files:
        if is_processed(state_dir, imp.sha256):
            logger.info("Already processed, skipping: %s", imp.path.name)
            continue
        try:
            readings = parse_file(imp, slot_minutes, known_meter_ids)
            all_readings.extend(readings)
            newly_processed.append(imp)
        except Exception as exc:
            logger.error("Failed to parse %s: %s", imp.path.name, exc)

    if not all_readings:
        logger.info("No new readings to process.")
        return

    all_readings = _filter_readings(
        all_readings, known_meter_ids, config.processing.unknown_meter_policy
    )

    if not all_readings:
        logger.info("No valid readings remain after meter filtering.")
        return

    if db_path is not None:
        save_meter_readings(db_path, all_readings)

    logger.info(
        "Processing %d reading(s) from %d new file(s)",
        len(all_readings), len(newly_processed),
    )

    slots = readings_to_slots(all_readings)
    match_results = match_all(slots)

    if not match_results:
        logger.warning("No valid match results produced — check reading quality flags.")
        return

    timestamps = [r.slot_start for r in match_results]
    period_start = min(timestamps)
    period_end = max(timestamps) + timedelta(minutes=slot_minutes)

    source_file_names = [imp.path.name for imp in newly_processed]
    billing_records = compute_billing(
        match_results, config, period_start, period_end,
        source_files=source_file_names,
    )

    if db_path is not None:
        run_id = uuid.uuid4().hex
        source_fingerprint = hashlib.sha256(
            "|".join(sorted(imp.sha256 for imp in newly_processed)).encode("utf-8")
        ).hexdigest()
        save_settlement_snapshot(
            db_path, run_id, source_fingerprint, period_start, period_end, billing_records,
        )

    meter_labels = {m.meter_id: m.label for m in config.meters}

    # Write reports — must succeed before touching state or archive
    report_paths = [
        write_billing_csv(billing_records, reports, period_start),
        write_billing_json(billing_records, reports, period_start),
        write_match_csv(match_results, reports, period_start),
        write_community_audit_csv(billing_records, reports, period_start),
        write_community_summary_json(
            billing_records, config.community.community_id, config.community.name, reports, period_start
        ),
        write_ledger_csv(match_results, reports, period_start, meter_labels=meter_labels),
    ]

    missing = [str(p) for p in report_paths if not p.exists()]
    if missing:
        raise RuntimeError(f"Report write verification failed: {missing}")

    report_names = [p.name for p in report_paths]

    for imp in newly_processed:
        mark_processed(state_dir, imp.sha256, imp.path.name, report_files=report_names)
        if config.processing.archive_processed:
            move_to_archive(imp.path, archive)

    logger.info(
        "Settlement cycle complete — period %s to %s",
        period_start.isoformat(), period_end.isoformat(),
    )

    # MQTT publish — after archive; failure does not invalidate the cycle
    if mqtt_client is not None:
        try:
            from shareomat.ha.mqtt_runtime import publish_billing, publish_system_state
            from shareomat.ha.mqtt_discovery import publish_ha_discovery

            publish_billing(mqtt_client, billing_records, config.mqtt)
            publish_system_state(
                mqtt_client, config.mqtt,
                status="ok",
                last_run=datetime.now(timezone.utc).isoformat(),
                inbox_count=sum(1 for f in inbox.iterdir() if f.is_file()),
                report_count=sum(1 for f in reports.iterdir() if f.is_file()),
                last_error="",
            )
            if config.mqtt.discovery_enabled:
                publish_ha_discovery(
                    mqtt_client, billing_records,
                    config.community.community_id, config.community.name, config.mqtt,
                )
        except Exception as exc:
            logger.error("MQTT publish failed (settlement cycle completed): %s", exc)
