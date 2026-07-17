# -*- coding: utf-8 -*-
"""
File: self_leg/core/raw/ebl_xlsx.py

Purpose:
    Provider-specific parser for EBL (Elektrizitätswerk Baselland) XLSX
    energy export files downloaded from the EBL customer portal.
    Extracts raw meter rows and delegates normalization to leg_normalizer.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    EBL XLSX export structure (as of 2026):
        Rows 1–7  metadata header (Messpunkt, Standort, Typ, …)
        Row  8    column labels (Datum, HT_NT, …)
        Row  9+   data: col A = timestamp (end-of-interval, Zürich local time)
                        col D = Bezug kWh (consumption from grid)
                        col E = Rücklieferung kWh (feed-in to grid)

    Timestamps mark the END of each 15-minute interval.
    Normalization (timezone conversion, end→start correction) is done
    by leg_normalizer.normalize_ebl_row().

    openpyxl is a soft dependency — imported inside parse() so the module
    loads cleanly even when openpyxl is not installed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from self_leg.leg_const import SLOT_MINUTES
from self_leg.models.meter import IntervalReading
from self_leg.core.pipeline.leg_normalizer import normalize_ebl_row

logger = logging.getLogger(__name__)

# ── EBL XLSX layout constants ─────────────────────────────────────────────────

_HEADER_ROWS = 8        # data starts at row 9 (index 8)
_ROW_MESSPUNKT = 3      # row 4 (0-indexed) contains the meter ID in col D
_COL_METER_ID = 3       # column D: meter ID in header rows
_COL_TIMESTAMP = 0      # column A: interval-end timestamp
_COL_BEZUG = 3          # column D: consumption (Bezug, import from grid)
_COL_RUECK = 4          # column E: feed-in (Rücklieferung, export to grid)


# ── Provider-specific raw row ─────────────────────────────────────────────────


@dataclass
class EblRow:
    """One raw EBL data row before normalization."""

    meter_id: str
    timestamp_end: datetime          # naive datetime in Zürich local time, end-of-interval
    bezug_kwh: float = 0.0           # consumption from grid (Bezug)
    ruecklieferung_kwh: float = 0.0  # feed-in to grid (Rücklieferung)
    source_file: str = ""


# ── Private helpers ───────────────────────────────────────────────────────────


def _extract_meter_id(rows: list) -> str:
    """Read the meter ID (Messpunkt) from the EBL XLSX header."""
    try:
        value = rows[_ROW_MESSPUNKT][_COL_METER_ID]
        return str(value).strip() if value else ""
    except (IndexError, TypeError):
        return ""


def _build_ebl_rows(rows: list, meter_id: str, source_file: str) -> list[EblRow]:
    """Convert raw XLSX rows into EblRow objects, skipping non-data rows."""
    result: list[EblRow] = []
    for row in rows[_HEADER_ROWS:]:
        ts = row[_COL_TIMESTAMP]
        if not isinstance(ts, datetime):
            continue
        result.append(EblRow(
            meter_id=meter_id,
            timestamp_end=ts,
            bezug_kwh=float(row[_COL_BEZUG] or 0.0),
            ruecklieferung_kwh=float(row[_COL_RUECK] or 0.0),
            source_file=source_file,
        ))
    return result


# ── Public interface ──────────────────────────────────────────────────────────


def parse(
    path: Path,
    slot_minutes: int = SLOT_MINUTES,
    known_meter_ids: set[str] | None = None,
) -> list[IntervalReading]:
    """Parse an EBL XLSX energy export into canonical 15-minute interval readings."""
    try:
        import openpyxl
    except ImportError:
        logger.error(
            "openpyxl is not installed — cannot parse EBL XLSX files. "
            "Run: pip install openpyxl"
        )
        return []

    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    wb.close()

    meter_id = _extract_meter_id(rows)
    if not meter_id:
        logger.error("%s: could not read meter ID (Messpunkt) from header", path.name)
        return []

    if known_meter_ids is not None and meter_id not in known_meter_ids:
        logger.warning(
            "%s: unknown meter_id '%s' — not in config",
            path.name, meter_id,
        )

    ebl_rows = _build_ebl_rows(rows, meter_id, path.name)

    readings: list[IntervalReading] = []
    for row in ebl_rows:
        readings.extend(normalize_ebl_row(row, slot_minutes))

    logger.info(
        "Parsed %d reading(s) from %s (meter_id: %s)",
        len(readings), path.name, meter_id,
    )
    return readings
