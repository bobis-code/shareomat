# -*- coding: utf-8 -*-
"""
File: shareomat/core/collector/sdat_import.py

Purpose:
    Imports received SDAT-CH files (stage SX-2): parse, check that they are
    addressed to this LEG and concern its metering points, then store the
    official series. A file is imported completely or not at all; the reason
    for a refusal stays visible and the file is checked again next time.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Import is not billing. It never creates, changes or releases a billing
    run; it only makes official data available. With the data source
    "SDAT (EBL/Swisseldex)" the billing then uses it (ADR-0008).

    Which series are this LEG's: a document may carry several communities
    (VSE example 1_12_3 does). The LEG's CommunityID is the one under which
    its own, known metering points appear. Once established it must not
    change; a different one is refused ("Falsche CommunityID"). An E31 carries
    no metering points and can only be assigned once an E66 established the
    CommunityID - until then it waits and is checked again.

    Fail closed: a series of this LEG with a metering point that is not in
    the master data refuses the whole document (a missing member would make
    the energy clearing wrong); after adding the meter the next import run
    takes it.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

from shareomat.core.pipeline.raw.sdat_ch import (
    DOCUMENT_TYPE_E66,
    SdatChValidationError,
    parse_sdat_ch_document,
    read_sdat_bytes,
    validate_receiver,
)
from shareomat.database.community import get_community
from shareomat.database.meters import list_meters
from shareomat.database.sdat_store import (
    OUTCOME_IMPORTED,
    document_outcome,
    established_community_id,
    record_rejection,
    store_document,
)

logger = logging.getLogger(__name__)

# One import at a time in this process (worker after a sync, button on the page).
_IMPORT_LOCK = threading.Lock()


@dataclass(frozen=True)
class ImportOutcome:
    sha256: str
    file_name: str
    imported: bool
    skipped: bool            # already imported earlier
    reason_code: str
    message: str
    document_type: str = ""
    series: int = 0


# ── Private helpers ───────────────────────────────────────────────────────────


def _own_community(document, known_meters: set[str], established: str) -> str:
    """The CommunityID of this LEG in `document`, or raise SdatChValidationError."""
    in_document = document.community_ids
    if document.header.document_type == DOCUMENT_TYPE_E66:
        by_meter = {s.community_id for s in document.series if s.metering_point_id in known_meters}
        if established and established in in_document:
            by_meter.add(established)
        if len(by_meter) > 1:
            raise SdatChValidationError(
                "COMMUNITY_CONFLICT",
                f"Eigene Messpunkte erscheinen unter mehreren CommunityIDs ({', '.join(sorted(by_meter))}).",
            )
        if not by_meter:
            raise SdatChValidationError(
                "NO_OWN_SERIES",
                "Keine Zeitreihe dieses Dokuments gehört zu einem erfassten Messpunkt dieser Gemeinschaft.",
            )
        own = by_meter.pop()
        if established and own != established:
            raise SdatChValidationError(
                "WRONG_COMMUNITY", f"CommunityID {own} weicht von der bisherigen {established} ab.",
            )
        return own
    if not established:
        raise SdatChValidationError(
            "COMMUNITY_UNKNOWN",
            "Die CommunityID dieser LEG ist noch nicht bekannt (sie ergibt sich aus der ersten E66-Lieferung). "
            "Die Datei wird beim nächsten Import erneut geprüft.",
        )
    if established not in in_document:
        raise SdatChValidationError("WRONG_COMMUNITY", f"Das Dokument enthält die CommunityID {established} nicht.")
    return established


def _check_meters(document, community_id: str, known_meters: set[str]) -> None:
    unknown = sorted({
        s.metering_point_id for s in document.series
        if s.community_id == community_id and s.metering_point_id and s.metering_point_id not in known_meters
    })
    if unknown:
        raise SdatChValidationError(
            "UNKNOWN_METERING_POINT",
            f"Unbekannte Messpunkte: {', '.join(unknown)}. Unter «Messpunkte» erfassen, dann erneut importieren.",
        )


# ── Public interface ──────────────────────────────────────────────────────────


def import_sdat_file(db_path: Path, path: Path, sha256: str, *, file_name: str, origin: str) -> ImportOutcome:
    """Check and import one file. Idempotent: an imported file is never imported twice."""
    if document_outcome(db_path, sha256) == OUTCOME_IMPORTED:
        return ImportOutcome(sha256, file_name, imported=True, skipped=True, reason_code="", message="bereits importiert")
    header = None
    try:
        document = parse_sdat_ch_document(read_sdat_bytes(path))
        header = document.header
        community = get_community(db_path)
        validate_receiver(header.receiver_eic, header.receiver_role, header.business_reason,
                          community.eic_code if community else "")
        known_meters = {m.meter_id.strip().upper() for m in list_meters(db_path)}
        community_id = _own_community(document, known_meters, established_community_id(db_path))
        _check_meters(document, community_id, known_meters)
    except SdatChValidationError as exc:
        record_rejection(db_path, sha256=sha256, file_name=file_name, origin=origin, header=header,
                         reason_code=exc.code, message=str(exc))
        logger.info("SDAT file %s not imported: %s", file_name, exc.code)
        return ImportOutcome(sha256, file_name, imported=False, skipped=False, reason_code=exc.code,
                             message=str(exc), document_type=header.document_type if header else "")
    note = "Quittung vom Absender verlangt (noch nicht geklärt, nicht gesendet)." if header.acknowledgement_requested else ""
    series = store_document(db_path, sha256=sha256, file_name=file_name, origin=origin, document=document,
                            community_id=community_id, message=note)
    logger.info("SDAT %s %s imported: %d series of community %s", header.document_type, file_name, series, community_id)
    return ImportOutcome(sha256, file_name, imported=True, skipped=False, reason_code="", message=note,
                         document_type=header.document_type, series=series)


def import_pending(db_path: Path, files: list[tuple[Path, str, str]], *, origin: str) -> list[ImportOutcome]:
    """Import every (path, sha256, name) that is not imported yet; E66 first so E31 can be assigned."""
    def order(item: tuple[Path, str, str]) -> int:
        try:
            head = read_sdat_bytes(item[0])[:4096]
        except SdatChValidationError:
            return 2
        return 0 if b"ValidatedMeteredData" in head else 1

    results = []
    with _IMPORT_LOCK:
        for path, sha256, name in sorted(files, key=order):
            if document_outcome(db_path, sha256) == OUTCOME_IMPORTED:
                continue
            results.append(import_sdat_file(db_path, path, sha256, file_name=name, origin=origin))
    return results
