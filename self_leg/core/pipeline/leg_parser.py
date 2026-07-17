# -*- coding: utf-8 -*-
"""
File: self_leg/core/leg_parser.py

Purpose:
    CSV and S-DAT XML parser for smart meter interval data.
    Normalizes raw readings into 15-minute EnergySlot objects
    with strict input validation.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    CSV format: timestamp, meter_id (or mpid for legacy files), value_kwh, direction [, quality]
    Both 'meter_id' and 'mpid' column names are accepted in CSV; 'mpid' is
    mapped to 'meter_id' immediately at import and is not used internally.
    S-DAT format: EXPERIMENTAL — basic XML structure only.
        Must be validated against real Swiss S-DAT files before production use.
        Real S-DAT structures can be significantly more complex than handled here.
"""

from __future__ import annotations

import csv
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from self_leg.leg_const import DIRECTION_EXPORT, DIRECTION_IMPORT, SLOT_MINUTES
from self_leg.models.meter import EnergySlot, IntervalReading
from self_leg.core.pipeline.leg_normalizer import snap_to_slot as _snap_to_slot

logger = logging.getLogger(__name__)

_VALID_DIRECTIONS = {DIRECTION_EXPORT, DIRECTION_IMPORT}


# ── Private helpers ───────────────────────────────────────────────────────────


def _parse_timestamp(value: str) -> datetime:
    """Parse an ISO timestamp string and verify it carries timezone information."""
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"timestamp '{value}' must be timezone-aware")
    return dt



def _xml_tag(ns_prefix: str, name: str) -> str:
    """Wrap an XML element name with its namespace prefix, if any."""
    return f"{ns_prefix}{name}"


def _read_meter_id(row: dict) -> str:
    """Read the meter identifier from a CSV row, accepting both 'meter_id' and legacy 'mpid'."""
    meter_id = row.get("meter_id") or row.get("mpid") or ""
    return meter_id.strip()


# ── Public functions ──────────────────────────────────────────────────────────


def parse_csv(
    path: Path,
    slot_minutes: int = SLOT_MINUTES,
    known_meter_ids: set[str] | None = None,
) -> list[IntervalReading]:
    """Parse a meter data CSV file into individual 15-minute energy readings."""
    readings: list[IntervalReading] = []
    seen: set[tuple[str, datetime, str]] = set()

    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for lineno, row in enumerate(reader, start=2):
            try:
                direction = row["direction"].strip().lower()
                if direction not in _VALID_DIRECTIONS:
                    raise ValueError(f"invalid direction '{direction}' — must be import or export")

                value = float(row["value_kwh"])
                if value < 0:
                    raise ValueError(f"value_kwh must be >= 0, got {value}")

                meter_id = _read_meter_id(row)
                if not meter_id:
                    raise ValueError("missing meter_id column")

                if known_meter_ids is not None and meter_id not in known_meter_ids:
                    logger.warning(
                        "%s:%d: unknown meter_id '%s' — not in config",
                        path.name, lineno, meter_id,
                    )

                slot = _snap_to_slot(_parse_timestamp(row["timestamp"]), slot_minutes)

                dedup_key = (meter_id, slot, direction)
                if dedup_key in seen:
                    logger.warning(
                        "%s:%d: duplicate row (meter_id=%s slot=%s dir=%s) — summing",
                        path.name, lineno, meter_id, slot.isoformat(), direction,
                    )
                seen.add(dedup_key)

                readings.append(IntervalReading(
                    meter_id=meter_id,
                    slot_start=slot,
                    value_kwh=value,
                    direction=direction,
                    quality=row.get("quality", "valid").strip(),
                    source_file=path.name,
                ))
            except (KeyError, ValueError) as exc:
                logger.warning("%s:%d: skipping row – %s", path.name, lineno, exc)

    logger.info("Parsed %d reading(s) from %s", len(readings), path.name)
    return readings


def parse_sdat(
    path: Path,
    slot_minutes: int = SLOT_MINUTES,
    known_meter_ids: set[str] | None = None,
) -> list[IntervalReading]:
    """Parse a Swiss S-DAT smart meter XML file into 15-minute energy readings (experimental)."""
    logger.warning(
        "S-DAT parser is experimental. Validate output against source file: %s",
        path.name,
    )
    readings: list[IntervalReading] = []
    try:
        tree = ET.parse(path)
    except ET.ParseError as exc:
        logger.error("Failed to parse XML %s: %s", path.name, exc)
        return readings

    root = tree.getroot()
    ns_prefix = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""

    for mp_elem in root.iter(_xml_tag(ns_prefix, "MeteringPoint")):
        mpid_elem = mp_elem.find(_xml_tag(ns_prefix, "MeteringPointID"))
        if mpid_elem is None or not mpid_elem.text:
            continue
        meter_id = mpid_elem.text.strip()

        if known_meter_ids is not None and meter_id not in known_meter_ids:
            logger.warning("%s: unknown meter_id '%s' — not in config", path.name, meter_id)

        dir_elem = mp_elem.find(_xml_tag(ns_prefix, "Direction"))
        direction = (
            dir_elem.text.strip().lower()
            if dir_elem is not None and dir_elem.text
            else DIRECTION_IMPORT
        )
        if direction not in _VALID_DIRECTIONS:
            logger.warning(
                "%s: invalid direction '%s' for meter '%s' — defaulting to import",
                path.name, direction, meter_id,
            )
            direction = DIRECTION_IMPORT

        for obs in mp_elem.iter(_xml_tag(ns_prefix, "Observation")):
            date_elem = obs.find(_xml_tag(ns_prefix, "ObservationDate"))
            vol_elem = obs.find(_xml_tag(ns_prefix, "Volume"))
            qual_elem = obs.find(_xml_tag(ns_prefix, "Quality"))
            if date_elem is None or vol_elem is None:
                continue
            try:
                value = float(vol_elem.text.strip())
                if value < 0:
                    raise ValueError(f"Volume must be >= 0, got {value}")
                readings.append(IntervalReading(
                    meter_id=meter_id,
                    slot_start=_snap_to_slot(_parse_timestamp(date_elem.text), slot_minutes),
                    value_kwh=value,
                    direction=direction,
                    quality=qual_elem.text.strip() if qual_elem is not None and qual_elem.text else "valid",
                    source_file=path.name,
                ))
            except (ValueError, AttributeError) as exc:
                logger.warning("%s: skipping observation – %s", path.name, exc)

    logger.info("Parsed %d reading(s) from %s", len(readings), path.name)
    return readings


def readings_to_slots(readings: list[IntervalReading]) -> dict[datetime, EnergySlot]:
    """Group raw meter readings into 15-minute energy buckets, one per timestamp."""
    slots: dict[datetime, EnergySlot] = {}

    for r in readings:
        if r.quality == "invalid":
            logger.debug("Dropping invalid reading meter_id=%s slot=%s", r.meter_id, r.slot_start)
            continue

        slot = slots.setdefault(r.slot_start, EnergySlot(slot_start=r.slot_start))

        if r.direction == DIRECTION_EXPORT:
            slot.producer_export[r.meter_id] = slot.producer_export.get(r.meter_id, 0.0) + r.value_kwh
        elif r.direction == DIRECTION_IMPORT:
            slot.consumer_import[r.meter_id] = slot.consumer_import.get(r.meter_id, 0.0) + r.value_kwh
        else:
            logger.warning("Unknown direction '%s' for meter_id=%s — skipping", r.direction, r.meter_id)

    logger.info("Aggregated %d reading(s) into %d slot(s)", len(readings), len(slots))
    return slots
