# -*- coding: utf-8 -*-
"""
File: shareomat/core/pipeline/raw/sdat_ch.py

Purpose:
    Parser for the official Swiss SDAT-CH 2025 metering data documents E66
    (individual series to the LEG representative, VSE process 1.12) and E31
    (LEG aggregates to the LEG representative, VSE process 1.13), as EBL
    sends them through the Swisseldex Datahub. See docs/sdat_leg_import.md.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    This is a DIFFERENT format from the simplified experimental XML parser
    in leg_parser.py:parse_sdat() (MeteringPoint/Observation, no
    namespaces, no E31/E66, no product IDs). That parser stays untouched.

    Implemented against the official VSE material (SDAT-CH 2025, Stand
    04.12.2025): ValidatedMeteredData_1p6.xsd, AggregatedMeteredData_1p3.xsd
    with their core components and code lists, the example messages
    1_12_3 (E66 to the LEG representative) and 1_13_3 (E31), and the process
    description (Messdatenaustauschprozesse 1.12/1.13). The parser does not
    run a full XSD validation (that needs the complete VSE/UNECE/eCH schema
    set and an extra library); it checks every element it uses against the
    types and code lists of those XSDs and refuses anything else.

    Needs verification with the first real EBL delivery: whether EBL fills
    every optional element like the VSE examples, and how a cancellation
    (Status 1) identifies the series it cancels (assumed: same metering
    point, flow, product and quarter hours).

    EIC format/check-character validation and the receiver gate
    (validate_receiver) do not depend on the XML structure; the EIC check
    character follows the public ENTSO-E EIC reference manual.
"""

from __future__ import annotations

import gzip
import re
import xml.etree.ElementTree as ET
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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


class SdatChValidationError(SdatChError):
    """Raised for a structurally parseable document that fails a business check.

    E.g. WRONG_RECEIVER / WRONG_ROLE / WRONG_BUSINESS_REASON / UNKNOWN_COMMUNITY
    from docs/sdat_leg_import.md §22. `code` carries that identifier so the
    import log can record why a message was rejected.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ── EIC (§9) ──────────────────────────────────────────────────────────────────

EIC_LENGTH: int = 16
_EIC_ALPHABET: str = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-"


def normalize_eic(value: str) -> str:
    """Strip whitespace and upper-case an EIC as typed by a user or found in XML."""
    return "".join(value.split()).upper()


def eic_check_char(first15: str) -> str:
    """Return the ENTSO-E check character for the first 15 EIC characters.

    Weights 16..2 over the character values (0-9, A-Z = 10-35, '-' = 36);
    check = 36 - ((sum - 1) mod 37). A result of '-' means no valid EIC can
    end that way (such codes are never issued).
    """
    total = sum(_EIC_ALPHABET.index(c) * (16 - i) for i, c in enumerate(first15))
    return _EIC_ALPHABET[36 - ((total - 1) % 37)]


def is_valid_eic(value: str) -> bool:
    """True if value (already normalized) is a 16-char EIC with a correct check character."""
    if len(value) != EIC_LENGTH or any(c not in _EIC_ALPHABET for c in value):
        return False
    check = eic_check_char(value[:15])
    return check != "-" and value[15] == check


def validate_receiver(
    receiver_eic: str,
    receiver_role: str,
    business_reason: str,
    configured_eic: str,
) -> None:
    """Gate an incoming SDAT message on its receiver before any value is used (§9–11).

    Raises SdatChValidationError unless the message is addressed to this
    community's own EIC in the CEM role for business reason C40. An unset
    configured EIC rejects everything — never accept by default.
    """
    configured = normalize_eic(configured_eic)
    if not configured:
        raise SdatChValidationError(
            "EIC_NOT_CONFIGURED",
            "Kein EIC für die Gemeinschaft hinterlegt — SDAT-Nachrichten können "
            "keinem Empfänger zugeordnet werden (Gemeinschaft → EIC).",
        )
    if normalize_eic(receiver_eic) != configured:
        raise SdatChValidationError(
            "WRONG_RECEIVER",
            f"Nachricht ist an EIC '{receiver_eic}' adressiert, nicht an den "
            f"eigenen EIC '{configured}'.",
        )
    if receiver_role.strip() != ROLE_CEM:
        raise SdatChValidationError(
            "WRONG_ROLE",
            f"Empfängerrolle '{receiver_role}' statt '{ROLE_CEM}'.",
        )
    if business_reason.strip() != BUSINESS_REASON_LEG:
        raise SdatChValidationError(
            "WRONG_BUSINESS_REASON",
            f"Business Reason '{business_reason}' statt '{BUSINESS_REASON_LEG}'.",
        )


# ── Document structure (VSE SDAT-CH 2025, Anhang 4 XSDs, Stand 04.12.2025) ─────

# Namespace of every element in ValidatedMeteredData_1p6.xsd and
# AggregatedMeteredData_1p3.xsd (rsm:...). Elements are matched by namespace
# and local name, never by the prefix the sender happens to use (§28).
NAMESPACE: str = "http://www.strom.ch"

DOCUMENT_TYPE_E66: str = "E66"   # individual series LGZ/EGZ to the LEG representative (VSE 1.12)
DOCUMENT_TYPE_E31: str = "E31"   # LEG aggregates LGS/EGS to the LEG representative (VSE 1.13)

# Root element -> the document type it must carry.
ROOT_ELEMENTS: dict[str, str] = {
    "ValidatedMeteredData_16": DOCUMENT_TYPE_E66,
    "AggregatedMeteredData_13": DOCUMENT_TYPE_E31,
}

# E31 AggregationCriteria/FlowCharacteristic (ebIX MeteringPointTypeCode).
FLOW_CONSUMPTION: str = "E17"
FLOW_PRODUCTION: str = "E18"

DOCUMENT_STATUSES: frozenset[str] = frozenset({STATUS_ORIGINAL, STATUS_REPLACE, STATUS_CANCELLATION})
CONDITIONS: frozenset[str] = frozenset({"", CONDITION_TEMPORARY, CONDITION_ESTIMATED})
COMMUNITY_TYPES: frozenset[str] = frozenset({COMMUNITY_TYPE_BASIC, COMMUNITY_TYPE_CONSOLIDATED})

RESOLUTION_MINUTES: int = 15
MEASURE_UNIT_KWH: str = "KWH"

# Size limits. The Datahub accepts up to 50 MB per file and 512 MB unpacked;
# a monthly E66 of a small LEG is a few MB. Far larger documents are not
# parsed in memory on a Home Assistant host - they need a look first.
MAX_DOCUMENT_BYTES: int = 64 * 1024 * 1024

_COMMUNITY_ID = re.compile(r"^[0-9]{6}-[0-9A-Z]{6}$")   # CommunityIDType pattern
_METERING_POINT_ID = re.compile(r"^CH[0-9A-Z]{31}$")     # 33-character Swiss metering point ID
_MAX_FRACTION_DIGITS = 3                                 # EnergyVolumeType fractionDigits=3
_XML_ENCODING = re.compile(rb"""^\s*<\?xml[^>]*encoding\s*=\s*["']([A-Za-z0-9._-]+)["']""")


@dataclass(frozen=True)
class SdatHeader:
    """HeaderInformation of one SDAT-CH document, as sent."""

    root_element: str
    document_type: str           # E66 | E31
    document_id: str
    status: str                  # 9 original | 5 replace | 1 cancellation
    created_at: datetime         # UTC
    sender_eic: str
    sender_role: str
    receiver_eic: str
    receiver_role: str
    business_reason: str
    report_start: datetime       # UTC
    report_end: datetime         # UTC
    acknowledgement_requested: bool   # ServiceTransaction@isIntelligibleCheckRequired


@dataclass(frozen=True)
class SdatSeries:
    """One MeteringData block: one flow of one metering point (E66) or of the LEG (E31).

    `values[i]` belongs to the quarter hour starting at
    interval_start + i * 15 min (Position i + 1). `conditions[i]` is "" for
    a plain value, "21" temporary or "56" estimated.
    """

    block_id: str
    metering_point_id: str       # E66 only, "" for E31
    grid_area_eic: str           # E31 only, "" for E66
    direction: str               # consumption | production
    product_id: str
    energy_type: str             # total | local | residual
    community_id: str
    community_type: str
    interval_start: datetime     # UTC
    interval_end: datetime       # UTC
    values: tuple[Decimal, ...]
    conditions: tuple[str, ...]


@dataclass(frozen=True)
class SdatDocument:
    header: SdatHeader
    series: tuple[SdatSeries, ...]

    @property
    def community_ids(self) -> set[str]:
        return {s.community_id for s in self.series}


# ── Private helpers ───────────────────────────────────────────────────────────


def _tag(name: str) -> str:
    return f"{{{NAMESPACE}}}{name}"


def _fail(code: str, message: str) -> SdatChValidationError:
    return SdatChValidationError(code, message)


def _child(parent: ET.Element, *path: str, required: bool = True) -> ET.Element | None:
    node: ET.Element | None = parent
    for name in path:
        node = node.find(_tag(name)) if node is not None else None
    if node is None and required:
        raise _fail("MISSING_ELEMENT", f"Element {'/'.join(path)} fehlt.")
    return node


def _text(parent: ET.Element, *path: str, required: bool = True) -> str:
    node = _child(parent, *path, required=required)
    value = (node.text or "").strip() if node is not None else ""
    if required and not value:
        raise _fail("MISSING_ELEMENT", f"Element {'/'.join(path)} ist leer.")
    return value


def _datetime(parent: ET.Element, *path: str) -> datetime:
    raw = _text(parent, *path)
    try:
        moment = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail("INVALID_DATETIME", f"{'/'.join(path)}: ungültiger Zeitpunkt '{raw}'.") from exc
    if moment.tzinfo is None:
        raise _fail("INVALID_DATETIME", f"{'/'.join(path)}: Zeitpunkt '{raw}' ohne Zeitzone.")
    return moment.astimezone(timezone.utc)


def _period(parent: ET.Element, name: str) -> tuple[datetime, datetime]:
    start = _datetime(parent, name, "StartDateTime")
    end = _datetime(parent, name, "EndDateTime")
    if end <= start:
        raise _fail("INVALID_PERIOD", f"{name}: Ende liegt nicht nach dem Beginn.")
    return start, end


def _check_quarter_hour(moment: datetime, what: str) -> None:
    if moment.second or moment.microsecond or moment.minute % RESOLUTION_MINUTES:
        raise _fail("INVALID_PERIOD", f"{what} {moment.isoformat()} liegt nicht auf einer Viertelstunde.")


def _volume(raw: str, where: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise _fail("INVALID_VOLUME", f"{where}: Wert '{raw}' ist keine Zahl.") from exc
    if not value.is_finite() or value < 0:
        raise _fail("INVALID_VOLUME", f"{where}: Wert '{raw}' ist nicht zulässig.")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -_MAX_FRACTION_DIGITS:
        raise _fail("INVALID_VOLUME", f"{where}: Wert '{raw}' hat mehr als 3 Nachkommastellen.")
    return value


def _product(block: ET.Element, block_id: str) -> tuple[str, str]:
    product_id_node = _child(block, "Product", "ID")
    codes = [(child.text or "").strip() for child in product_id_node if (child.text or "").strip()]
    if len(codes) != 1:
        raise _fail("INVALID_PRODUCT", f"{block_id}: Produkt-ID fehlt oder ist mehrdeutig.")
    product_id = codes[0]
    energy_type = PRODUCT_ID_TO_ENERGY_TYPE.get(product_id)
    if energy_type is None:
        raise _fail("UNSUPPORTED_PRODUCT", f"{block_id}: Produkt {product_id} ist für LEG nicht vorgesehen.")
    unit = _text(block, "Product", "MeasureUnit")
    if unit != MEASURE_UNIT_KWH:
        raise _fail("UNSUPPORTED_UNIT", f"{block_id}: Einheit {unit} statt {MEASURE_UNIT_KWH}.")
    return product_id, energy_type


def _resolution(block: ET.Element, block_id: str) -> None:
    node = _child(block, "Resolution", required=False)
    if node is None:
        return  # optional in ValidatedMeteredData; the class diagram fixes it to 15 min
    resolution = _text(node, "Resolution")
    unit = _text(node, "Unit")
    if resolution != str(RESOLUTION_MINUTES) or unit != "MIN":
        raise _fail("UNSUPPORTED_RESOLUTION", f"{block_id}: Auflösung {resolution} {unit} statt 15 MIN.")


def _identity(block: ET.Element, document_type: str, block_id: str) -> tuple[str, str, str]:
    """(metering_point_id, grid_area_eic, direction) of one block."""
    if document_type == DOCUMENT_TYPE_E66:
        consumption = _child(block, "ConsumptionMeteringPoint", required=False)
        production = _child(block, "ProductionMeteringPoint", required=False)
        if (consumption is None) == (production is None):
            raise _fail("INVALID_METERING_POINT",
                        f"{block_id}: genau ein Verbrauchs- oder Produktionsmesspunkt erwartet.")
        node = consumption if consumption is not None else production
        metering_point = _text(node, "VSENationalID").upper()
        if not _METERING_POINT_ID.match(metering_point):
            raise _fail("INVALID_METERING_POINT", f"{block_id}: Messpunkt-ID '{metering_point}' ist ungültig.")
        direction = DIRECTION_CONSUMPTION if consumption is not None else DIRECTION_PRODUCTION
        return metering_point, "", direction
    grid_area = normalize_eic(_text(block, "MeteringGridArea", "EICID"))
    flow = _text(block, "AggregationCriteria", "FlowCharacteristic")
    if flow == FLOW_CONSUMPTION:
        return "", grid_area, DIRECTION_CONSUMPTION
    if flow == FLOW_PRODUCTION:
        return "", grid_area, DIRECTION_PRODUCTION
    raise _fail("INVALID_FLOW", f"{block_id}: FlowCharacteristic {flow} statt E17/E18.")


def _observations(block: ET.Element, start: datetime, end: datetime, block_id: str) -> tuple[tuple, tuple]:
    """Values and conditions in position order; every position 1..N exactly once."""
    expected = int((end - start) / timedelta(minutes=RESOLUTION_MINUTES))
    by_position: dict[int, tuple[Decimal, str]] = {}
    for observation in block.findall(_tag("Observation")):
        raw_position = _text(observation, "Position", "Sequence")
        if not raw_position.isdigit():
            raise _fail("INVALID_POSITION", f"{block_id}: Position '{raw_position}' ist ungültig.")
        position = int(raw_position)
        if not 1 <= position <= expected:
            raise _fail("INVALID_POSITION", f"{block_id}: Position {position} ausserhalb 1..{expected}.")
        if position in by_position:
            raise _fail("DUPLICATE_POSITION", f"{block_id}: Position {position} doppelt.")
        volume = _volume(_text(observation, "Volume"), f"{block_id} Position {position}")
        condition = _text(observation, "Condition", required=False)
        if condition not in CONDITIONS:
            raise _fail("INVALID_CONDITION", f"{block_id} Position {position}: Qualität {condition} unbekannt.")
        by_position[position] = (volume, condition)
    missing = expected - len(by_position)
    if missing:
        raise _fail("INCOMPLETE_SERIES", f"{block_id}: {missing} von {expected} Viertelstunden fehlen.")
    ordered = [by_position[p] for p in range(1, expected + 1)]
    return tuple(v for v, _ in ordered), tuple(c for _, c in ordered)


def _header(root: ET.Element, root_name: str) -> SdatHeader:
    header = _child(root, root_name.split("_")[0] + "_HeaderInformation")
    document_type = _text(header, "InstanceDocument", "DocumentType", "ebIXCode")
    if document_type != ROOT_ELEMENTS[root_name]:
        raise _fail("WRONG_DOCUMENT_TYPE", f"Dokumenttyp {document_type} passt nicht zu {root_name}.")
    status = _text(header, "InstanceDocument", "Status")
    if status not in DOCUMENT_STATUSES:
        raise _fail("UNSUPPORTED_STATUS", f"Dokumentstatus {status} wird nicht unterstützt.")
    report_start, report_end = _period(_child(header, "BusinessScopeProcess"), "ReportPeriod")
    transaction = _child(header, "BusinessScopeProcess", "BusinessService", "ServiceTransaction", required=False)
    ack = transaction is not None and (transaction.get("isIntelligibleCheckRequired") or "").strip().lower() == "true"
    return SdatHeader(
        root_element=root_name,
        document_type=document_type,
        document_id=_text(header, "InstanceDocument", "DocumentID"),
        status=status,
        created_at=_datetime(header, "InstanceDocument", "Creation"),
        sender_eic=normalize_eic(_text(header, "Sender", "ID", "EICID")),
        sender_role=_text(header, "Sender", "Role"),
        receiver_eic=normalize_eic(_text(header, "Receiver", "ID", "EICID")),
        receiver_role=_text(header, "Receiver", "Role"),
        business_reason=_text(header, "BusinessScopeProcess", "BusinessReasonType", "VSENationalCode"),
        report_start=report_start,
        report_end=report_end,
        acknowledgement_requested=ack,
    )


def _series(block: ET.Element, header: SdatHeader, index: int) -> SdatSeries:
    block_id = _text(block, "DocumentID", required=False) or f"MeteringData #{index}"
    start, end = _period(block, "Interval")
    _check_quarter_hour(start, f"{block_id}: Beginn")
    _check_quarter_hour(end, f"{block_id}: Ende")
    if start < header.report_start or end > header.report_end:
        raise _fail("OUTSIDE_REPORT_PERIOD", f"{block_id}: Zeitreihe liegt ausserhalb der Berichtsperiode.")
    _resolution(block, block_id)
    product_id, energy_type = _product(block, block_id)
    metering_point, grid_area, direction = _identity(block, header.document_type, block_id)
    community_id = _text(block, "Community", "CommunityID")
    if not _COMMUNITY_ID.match(community_id):
        raise _fail("INVALID_COMMUNITY", f"{block_id}: CommunityID '{community_id}' ist ungültig.")
    community_type = _text(block, "Community", "CommunityType", "VSENationalCode")
    if community_type not in COMMUNITY_TYPES:
        raise _fail("INVALID_COMMUNITY", f"{block_id}: CommunityType {community_type} unbekannt.")
    values, conditions = _observations(block, start, end, block_id)
    return SdatSeries(
        block_id=block_id, metering_point_id=metering_point, grid_area_eic=grid_area, direction=direction,
        product_id=product_id, energy_type=energy_type, community_id=community_id,
        community_type=community_type, interval_start=start, interval_end=end,
        values=values, conditions=conditions,
    )


def _check_consistency(series: list[SdatSeries]) -> None:
    """No flow twice for overlapping quarter hours, and one type per community.

    One document may carry several communities (the VSE examples 1_12_3 and
    1_13_3 do: a representative can represent more than one LEG). Which
    series belong to this Shareomat's LEG is decided by the importer.
    """
    types: dict[str, str] = {}
    for s in series:
        if types.setdefault(s.community_id, s.community_type) != s.community_type:
            raise _fail("INVALID_COMMUNITY", f"{s.block_id}: CommunityType für {s.community_id} widersprüchlich.")
    seen: dict[tuple, list[tuple[datetime, datetime]]] = {}
    for s in series:
        key = (s.community_id, s.metering_point_id, s.grid_area_eic, s.direction, s.energy_type)
        for other_start, other_end in seen.get(key, []):
            if s.interval_start < other_end and other_start < s.interval_end:
                raise _fail("DUPLICATE_SERIES", f"{s.block_id}: Zeitreihe doppelt im Dokument.")
        seen.setdefault(key, []).append((s.interval_start, s.interval_end))


# ── Public interface ──────────────────────────────────────────────────────────


def read_sdat_bytes(path: Path) -> bytes:
    """File content, unpacked if it ends in .gz, never more than MAX_DOCUMENT_BYTES."""
    if path.name.lower().endswith(".gz"):
        opener = gzip.open
    else:
        opener = open
    try:
        with opener(path, "rb") as stream:
            data = stream.read(MAX_DOCUMENT_BYTES + 1)
    except (OSError, EOFError, zlib.error) as exc:
        raise _fail("UNREADABLE_FILE", f"Datei kann nicht gelesen werden: {exc}") from exc
    if len(data) > MAX_DOCUMENT_BYTES:
        raise _fail("FILE_TOO_LARGE", f"Dokument grösser als {MAX_DOCUMENT_BYTES // (1024 * 1024)} MB.")
    return data


def parse_sdat_ch_document(data: bytes) -> SdatDocument:
    """Parse and structurally validate one SDAT-CH E66/E31 document.

    Everything that is used is checked against the VSE XSD and process
    description (1.12/1.13); anything unexpected raises SdatChValidationError
    with a code instead of guessing. The receiver check against the own EIC
    is the importer's job (validate_receiver), it needs the configuration.

    DTDs and entities are refused before parsing (no XXE, no entity
    expansion); SDAT documents never contain them.
    """
    if len(data) > MAX_DOCUMENT_BYTES:
        raise _fail("FILE_TOO_LARGE", "Dokument zu gross.")
    # Only UTF-8 (SDAT-CH documents are UTF-8): another encoding could hide a
    # DTD from the byte check below.
    head = data[:200]
    declared = _XML_ENCODING.search(head)
    if b"\x00" in head or head.startswith((b"\xff\xfe", b"\xfe\xff")) or (
            declared and declared.group(1).lower() not in (b"utf-8", b"utf8")):
        raise _fail("UNSUPPORTED_ENCODING", "Nur UTF-8-kodierte SDAT-Dokumente werden verarbeitet.")
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise _fail("FORBIDDEN_XML", "Dokument enthält DTD- oder Entity-Deklarationen.")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise _fail("INVALID_XML", f"Kein gültiges XML: {exc}") from exc
    namespace, _, root_name = root.tag[1:].partition("}") if root.tag.startswith("{") else ("", "", root.tag)
    if namespace != NAMESPACE or root_name not in ROOT_ELEMENTS:
        raise _fail("UNSUPPORTED_DOCUMENT", f"Kein unterstütztes SDAT-CH-Dokument (Wurzelelement {root_name}).")
    header = _header(root, root_name)
    blocks = root.findall(_tag("MeteringData"))
    if not blocks:
        raise _fail("NO_DATA", "Das Dokument enthält keine Zeitreihen.")
    series = [_series(block, header, index) for index, block in enumerate(blocks, start=1)]
    _check_consistency(series)
    return SdatDocument(header=header, series=tuple(series))


def parse_sdat_ch(
    path: Path,
    slot_minutes: int = SLOT_MINUTES,
    known_meter_ids: set[str] | None = None,
) -> list[IntervalReading]:
    """E66 total energy (the physical meter data) as 15-minute IntervalReadings.

    For the file inbox of the automatic engine (meter_data_source "sdat_leg").
    Only the measured total (8716867000030) becomes a reading - consumption as
    "import", production as "export". The grid operator's LEG/residual split
    is not an IntervalReading: it is stored and billed through
    shareomat.database.sdat_store (see ADR-0008), never redistributed.
    E31 documents carry no metering point and yield no readings.
    """
    if slot_minutes != RESOLUTION_MINUTES:
        raise _fail("UNSUPPORTED_RESOLUTION", f"SDAT-CH liefert 15 Minuten, nicht {slot_minutes}.")
    document = parse_sdat_ch_document(read_sdat_bytes(path))
    readings: list[IntervalReading] = []
    if document.header.document_type != DOCUMENT_TYPE_E66 or document.header.status == STATUS_CANCELLATION:
        return readings
    for series in document.series:
        if series.energy_type != "total":
            continue
        if known_meter_ids is not None and series.metering_point_id not in known_meter_ids:
            continue
        direction = "import" if series.direction == DIRECTION_CONSUMPTION else "export"
        for index, (value, condition) in enumerate(zip(series.values, series.conditions)):
            readings.append(IntervalReading(
                meter_id=series.metering_point_id,
                slot_start=series.interval_start + timedelta(minutes=RESOLUTION_MINUTES * index),
                value_kwh=float(value),
                direction=direction,
                quality={"": "valid", CONDITION_TEMPORARY: "temporary", CONDITION_ESTIMATED: "estimated"}[condition],
                source_file=path.name,
            ))
    return readings
