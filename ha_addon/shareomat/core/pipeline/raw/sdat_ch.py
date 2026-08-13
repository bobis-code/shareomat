# -*- coding: utf-8 -*-
"""
File: shareomat/core/pipeline/raw/sdat_ch.py

Purpose:
    Parser for the official Swiss SDAT-CH-2025 metering data exchange
    format (E31/E66), as used by EBL's LEG-Service via the swisseldex
    Datahub. See docs/sdat_leg_import.md for the full design.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    This is a DIFFERENT format from the simplified experimental XML parser
    in leg_parser.py:parse_sdat() (MeteringPoint/Observation, no
    namespaces, no E31/E66, no product IDs). That parser stays untouched
    for whatever already relies on it. This module only becomes active
    when a community's OperationSettings.meter_data_source is set to
    METER_DATA_SOURCE_SDAT_LEG (see shareomat.core.leg_runner.parse_file).

    BLOCKED, like shareomat.external_data.ebl: the exact XML namespaces,
    element paths, document/message-ID structure, status- and
    condition-code semantics, and interval→timestamp construction (incl.
    DST) are NOT guessed here — no official SDAT-CH-2025 XSDs or example
    messages are available in this repo yet (docs/sdat_leg_import.md §32).
    parse_sdat_ch() raises SdatChNotImplementedError until they are and the
    parsing logic below is implemented against them. Everything in
    _constants.py-equivalent section below (product IDs, role/business
    reason/status/condition codes, community types) IS confirmed by the
    reference document and safe to rely on once parsing exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from shareomat.models.meter_data import IntervalReading
from shareomat.leg_const import SLOT_MINUTES

# ── Product IDs (docs/sdat_leg_import.md §3) ───────────────────────────────────

PRODUCT_ID_LEG_TOTAL: str = "8716867000030"     # LEG Total Energy
PRODUCT_ID_LEG_LOCAL: str = "2404050010123"     # LEG Energy (locally shared)
PRODUCT_ID_LEG_RESIDUAL: str = "2404050010124"  # Residual Energy

PRODUCT_ID_TO_ENERGY_TYPE: dict[str, str] = {
    PRODUCT_ID_LEG_TOTAL: "total",
    PRODUCT_ID_LEG_LOCAL: "local",
    PRODUCT_ID_LEG_RESIDUAL: "residual",
}

# ── Directions (§4) ─────────────────────────────────────────────────────────────

DIRECTION_CONSUMPTION: str = "consumption"
DIRECTION_PRODUCTION: str = "production"

# ── Role (§10) ────────────────────────────────────────────────────────────────

ROLE_CEM: str = "CEM"  # Community Energy Manager — required receiver role

# ── Business reason (§11) ────────────────────────────────────────────────────

BUSINESS_REASON_LEG: str = "C40"

# ── Community type (§13) ─────────────────────────────────────────────────────

COMMUNITY_TYPE_BASIC: str = "CT01"          # Basis LEG
COMMUNITY_TYPE_CONSOLIDATED: str = "CT02"   # Zusammenfassende Rechnung LEG

# ── Document status (§14) ────────────────────────────────────────────────────

STATUS_ORIGINAL: str = "9"
STATUS_REPLACE: str = "5"
STATUS_CANCELLATION: str = "1"

# ── Condition / quality codes (§15) ──────────────────────────────────────────

CONDITION_TEMPORARY: str = "21"
CONDITION_ESTIMATED: str = "56"

# ── Error classes (§22) ──────────────────────────────────────────────────────


class SdatChError(Exception):
    """Base class for all SDAT-CH-2025 import errors."""


class SdatChNotImplementedError(SdatChError, NotImplementedError):
    """Raised by parse_sdat_ch() until it is implemented against verified XSDs.

    Mirrors shareomat.external_data.ebl.EblNotImplementedError: raise a
    clear, actionable error instead of guessing at XML structure that
    would silently produce wrong meter readings feeding into billing.
    """

    def __init__(self, function_name: str) -> None:
        super().__init__(
            f"{function_name}() ist noch nicht implementiert. Die exakte "
            "SDAT-CH-2025-Struktur (Namespaces, Elementpfade, Status-/"
            "Condition-Code-Semantik) ist noch nicht anhand offizieller "
            "XSDs/Beispieldateien verifiziert — siehe docs/sdat_leg_import.md "
            "§32. Community weiterhin auf 'email_csv' belassen, bis dieser "
            "Parser fertiggestellt ist."
        )


class SdatChValidationError(SdatChError):
    """Raised for a structurally parseable document that fails a business check.

    E.g. WRONG_RECEIVER / WRONG_ROLE / WRONG_BUSINESS_REASON / UNKNOWN_COMMUNITY
    from docs/sdat_leg_import.md §22 — reserved for use once validation.py
    equivalent logic is implemented alongside the real parser.
    """


# ── Normalized record (§20, reduced to what leg_runner's pipeline expects) ──────


@dataclass
class SdatChObservation:
    """One parsed SDAT-CH observation, prior to conversion into IntervalReading.

    Kept distinct from IntervalReading because SDAT carries more than the
    current pipeline model does (energy_type, product_id, document_id,
    status, condition code as a raw string) — see docs/sdat_leg_import.md
    §20 NormalizedMeteringData. Folding those into IntervalReading, or
    introducing a NormalizedMeteringData/MeteringValueVersion table, is
    Phase 4/5/18 work and intentionally not done until the parser itself
    exists.
    """

    metering_point_id: str
    direction: str          # consumption | production
    energy_type: str         # total | local | residual
    product_id: str
    value_kwh: float
    condition_code: str
    document_id: str
    status: str


# ── Public interface ──────────────────────────────────────────────────────────


def parse_sdat_ch(
    path: Path,
    slot_minutes: int = SLOT_MINUTES,
    known_meter_ids: set[str] | None = None,
) -> list[IntervalReading]:
    """Parse a SDAT-CH-2025 E31/E66 XML document into 15-minute interval readings.

    Not yet implemented — see SdatChNotImplementedError and
    docs/sdat_leg_import.md §32 for exactly what is still unverified.
    """
    raise SdatChNotImplementedError("parse_sdat_ch")
