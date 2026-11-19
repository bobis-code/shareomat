# -*- coding: utf-8 -*-
"""
File: shareomat/database/sdat_store.py

Purpose:
    Persistence of official SDAT-CH E66/E31 series (stage SX-2) and the
    rule which delivered value counts for a quarter hour ("effective" value),
    used by the official billing path (stage SX-3, ADR-0008).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Additive tables sdat_documents / sdat_series (schema 17). Nothing here
    touches meter_readings or any existing table: CSV/Excel imports and the
    existing billing are unaffected.

    Every delivery is kept (the grid operator sends the last 5 days daily,
    non-plausibilised, and the plausibilised month by the 5th working day -
    VSE Messdatenaustauschprozesse 1.12/1.13). Which one counts, per
    (metering point or grid area, direction, energy type, quarter hour):

    * deliveries are applied in the order the grid operator created them
      (header Creation, then arrival);
    * a newer value replaces an older one ...
    * ... except a temporary value (condition 21) never replaces a value that
      was already plausibilised - that would lower the quality; it is
      counted as an anomaly instead;
    * a cancellation document (status 1) removes the value it repeats.

    Assumption / needs verification with real EBL data: a cancellation
    repeats the series it cancels (same metering point, flow, product,
    quarter hours).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from shareomat.database.sqlite import connect, now_iso

OUTCOME_IMPORTED = "imported"
OUTCOME_REJECTED = "rejected"

_SLOT = timedelta(minutes=15)
_CONDITION_TEMPORARY = "21"
# Values arrive with 3 decimals: total and local + residual may differ by this much (O3, 18.11.2026).
SDAT_RESOLUTION_KWH = Decimal("0.001")
_STATUS_CANCELLATION = "1"


@dataclass(frozen=True)
class OfficialValue:
    value: Decimal
    condition: str          # "" | "21" | "56"
    sha256: str             # document it came from


@dataclass
class OfficialData:
    """Effective official values of one document type in a period."""

    values: dict[tuple[str, str, str, datetime], OfficialValue] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)   # sha256 -> file name of documents in use
    downgrades: int = 0      # temporary values that arrived after a plausibilised one (ignored)
    community_id: str = ""


@dataclass(frozen=True)
class DeliveredValue:
    """What one delivery said about one quarter hour of one metering point (raw, before the rule above)."""

    file_name: str
    sha256: str
    created_at: str          # header Creation (UTC ISO), "" if unknown
    cancellation: bool
    values: dict[tuple[str, str], tuple[Decimal, str]]   # (direction, energy type) -> (kWh, condition)


@dataclass(frozen=True)
class SdatDocumentRecord:
    sha256: str
    file_name: str
    origin: str
    document_type: str
    document_id: str
    status: str
    outcome: str
    reason_code: str
    message: str
    series_count: int
    foreign_series_count: int
    acknowledgement_requested: bool
    community_id: str
    report_start: str | None
    report_end: str | None
    checked_at: str


# ── Private helpers ───────────────────────────────────────────────────────────


def _iso(moment: datetime | None) -> str | None:
    return moment.astimezone(timezone.utc).isoformat() if moment is not None else None


def _encode(values, conditions) -> str:
    return "|".join(f"{value};{condition}" for value, condition in zip(values, conditions))


def _decode(observations: str) -> list[tuple[Decimal, str]]:
    result = []
    for item in observations.split("|"):
        value, _, condition = item.partition(";")
        result.append((Decimal(value), condition))
    return result


def _row_to_record(row) -> SdatDocumentRecord:
    return SdatDocumentRecord(
        sha256=row["sha256"], file_name=row["file_name"], origin=row["origin"],
        document_type=row["document_type"], document_id=row["document_id"], status=row["status"],
        outcome=row["outcome"], reason_code=row["reason_code"], message=row["message"],
        series_count=row["series_count"], foreign_series_count=row["foreign_series_count"],
        acknowledgement_requested=bool(row["acknowledgement_requested"]), community_id=row["community_id"],
        report_start=row["report_start"], report_end=row["report_end"], checked_at=row["checked_at"],
    )


def _upsert_document(conn, *, sha256: str, file_name: str, origin: str, header, outcome: str,
                     reason_code: str, message: str, community_id: str, series_count: int,
                     foreign_series_count: int) -> int:
    now = now_iso()
    values = {
        "file_name": file_name, "origin": origin,
        "document_type": header.document_type if header else "",
        "document_id": header.document_id if header else "",
        "status": header.status if header else "",
        "source_created_at": _iso(header.created_at) if header else None,
        "sender_eic": header.sender_eic if header else "",
        "sender_role": header.sender_role if header else "",
        "report_start": _iso(header.report_start) if header else None,
        "report_end": _iso(header.report_end) if header else None,
        "acknowledgement_requested": int(bool(header and header.acknowledgement_requested)),
        "community_id": community_id, "outcome": outcome, "reason_code": reason_code, "message": message,
        "series_count": series_count, "foreign_series_count": foreign_series_count, "checked_at": now,
    }
    row = conn.execute("SELECT id, outcome FROM sdat_documents WHERE sha256 = ?", (sha256,)).fetchone()
    if row is None:
        columns = ", ".join(["sha256", "first_checked_at", *values])
        marks = ", ".join("?" for _ in range(len(values) + 2))
        cursor = conn.execute(f"INSERT INTO sdat_documents ({columns}) VALUES ({marks})",
                              (sha256, now, *values.values()))
        return int(cursor.lastrowid)
    if row["outcome"] == OUTCOME_IMPORTED:
        raise ValueError(f"document {sha256} is already imported and is never replaced")
    assignments = ", ".join(f"{name} = ?" for name in values)
    conn.execute(f"UPDATE sdat_documents SET {assignments} WHERE id = ?", (*values.values(), row["id"]))
    return int(row["id"])


# ── Public functions ──────────────────────────────────────────────────────────


def document_outcome(db_path: Path, sha256: str) -> str | None:
    """imported / rejected for a known file, None if it was never checked."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT outcome FROM sdat_documents WHERE sha256 = ?", (sha256,)).fetchone()
    return row["outcome"] if row else None


def established_community_id(db_path: Path) -> str:
    """The CommunityID this LEG has in the grid operator's data (from imported E66), or ""."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT DISTINCT community_id FROM sdat_documents WHERE outcome = ? AND community_id != ''",
            (OUTCOME_IMPORTED,),
        ).fetchall()
    ids = sorted(r["community_id"] for r in rows)
    return ids[0] if len(ids) == 1 else ""


def record_rejection(db_path: Path, *, sha256: str, file_name: str, origin: str, header, reason_code: str,
                     message: str) -> None:
    """Remember why a file was not imported (updated when it is checked again)."""
    with connect(db_path) as conn:
        with conn:
            _upsert_document(conn, sha256=sha256, file_name=file_name, origin=origin, header=header,
                             outcome=OUTCOME_REJECTED, reason_code=reason_code, message=message,
                             community_id="", series_count=0, foreign_series_count=0)


def store_document(db_path: Path, *, sha256: str, file_name: str, origin: str, document, community_id: str,
                   message: str = "") -> int:
    """Store the series of `community_id` from a parsed SdatDocument in one transaction."""
    own = [s for s in document.series if s.community_id == community_id]
    with connect(db_path) as conn:
        with conn:
            document_ref = _upsert_document(
                conn, sha256=sha256, file_name=file_name, origin=origin, header=document.header,
                outcome=OUTCOME_IMPORTED, reason_code="", message=message, community_id=community_id,
                series_count=len(own), foreign_series_count=len(document.series) - len(own),
            )
            conn.executemany(
                """
                INSERT INTO sdat_series (document_ref, block_id, metering_point_id, grid_area_eic, direction,
                    energy_type, product_id, community_id, interval_start, interval_end, observations)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (document_ref, s.block_id, s.metering_point_id, s.grid_area_eic, s.direction, s.energy_type,
                     s.product_id, s.community_id, _iso(s.interval_start), _iso(s.interval_end),
                     _encode(s.values, s.conditions))
                    for s in own
                ],
            )
    return len(own)


def load_official_values(db_path: Path, start: datetime, end: datetime, *, document_type: str = "E66",
                         metering_point_ids: Iterable[str] | None = None) -> OfficialData:
    """Effective values per (metering point | grid area, direction, energy type, quarter hour) in [start, end).

    Key[0] is the metering point for E66 and the grid area EIC for E31.
    Only documents of the established community are used. `metering_point_ids`
    (E66 only) limits the result to these points; the rule per value is the same.
    """
    data = OfficialData(community_id=established_community_id(db_path))
    if not data.community_id:
        return data
    point_filter, point_params = "", []
    if metering_point_ids is not None:
        point_params = sorted({point.strip().upper() for point in metering_point_ids})
        if not point_params:
            return data
        point_filter = f" AND UPPER(s.metering_point_id) IN ({', '.join('?' * len(point_params))})"
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.metering_point_id, s.grid_area_eic, s.direction, s.energy_type, s.interval_start,
                   s.observations, d.sha256, d.file_name, d.status, d.source_created_at, d.id AS doc_id
            FROM sdat_series s JOIN sdat_documents d ON d.id = s.document_ref
            WHERE d.outcome = ? AND d.document_type = ? AND s.community_id = ?
              AND s.interval_start < ? AND s.interval_end > ?""" + point_filter + """
            ORDER BY d.source_created_at, d.id
            """,
            (OUTCOME_IMPORTED, document_type, data.community_id, _iso(end), _iso(start), *point_params),
        ).fetchall()
    current: dict[tuple, tuple[OfficialValue, str] | None] = {}
    for row in rows:
        target = row["metering_point_id"] or row["grid_area_eic"]
        slot = datetime.fromisoformat(row["interval_start"])
        cancel = row["status"] == _STATUS_CANCELLATION
        for value, condition in _decode(row["observations"]):
            if start <= slot < end:
                key = (target, row["direction"], row["energy_type"], slot)
                previous = current.get(key)
                if cancel:
                    current[key] = None
                elif (previous is not None and condition == _CONDITION_TEMPORARY
                      and previous[0].condition != _CONDITION_TEMPORARY):
                    data.downgrades += 1
                else:
                    current[key] = (OfficialValue(value, condition, row["sha256"]), row["file_name"])
            slot += _SLOT
    for key, entry in current.items():
        if entry is not None:
            data.values[key] = entry[0]
            data.sources[entry[0].sha256] = entry[1]
    return data


def official_data_end(db_path: Path) -> datetime | None:
    """End of the latest quarter hour any imported E66 delivery of this LEG covers, None without data."""
    community_id = established_community_id(db_path)
    if not community_id:
        return None
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT MAX(s.interval_end) AS last FROM sdat_series s JOIN sdat_documents d ON d.id = s.document_ref
            WHERE d.outcome = ? AND d.document_type = 'E66' AND s.community_id = ? AND s.metering_point_id != ''
            """,
            (OUTCOME_IMPORTED, community_id),
        ).fetchone()
    return datetime.fromisoformat(row["last"]) if row and row["last"] else None


def deliveries_of_quarter_hour(db_path: Path, metering_point_id: str, slot: datetime) -> list[DeliveredValue]:
    """Every imported E66 delivery that contains this quarter hour of this point, oldest first.

    Raw view for "where does this value come from": the effective value is
    still the one load_official_values() picks from these.
    """
    community_id = established_community_id(db_path)
    if not community_id:
        return []
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT s.direction, s.energy_type, s.interval_start, s.observations,
                   d.id AS doc_id, d.sha256, d.file_name, d.status, d.source_created_at
            FROM sdat_series s JOIN sdat_documents d ON d.id = s.document_ref
            WHERE d.outcome = ? AND d.document_type = 'E66' AND s.community_id = ?
              AND UPPER(s.metering_point_id) = ? AND s.interval_start <= ? AND s.interval_end > ?
            ORDER BY d.source_created_at, d.id
            """,
            (OUTCOME_IMPORTED, community_id, metering_point_id.strip().upper(), _iso(slot), _iso(slot)),
        ).fetchall()
    deliveries: dict[int, DeliveredValue] = {}
    for row in rows:
        index = int((slot - datetime.fromisoformat(row["interval_start"])) / _SLOT)
        observations = _decode(row["observations"])
        if not 0 <= index < len(observations):
            continue
        delivery = deliveries.setdefault(row["doc_id"], DeliveredValue(
            file_name=row["file_name"], sha256=row["sha256"], created_at=row["source_created_at"] or "",
            cancellation=row["status"] == _STATUS_CANCELLATION, values={}))
        delivery.values[(row["direction"], row["energy_type"])] = observations[index]
    return list(deliveries.values())


def imported_period(db_path: Path, sha256: str) -> tuple[datetime, datetime] | None:
    """First and last quarter hour covered by one imported document's own series."""
    with connect(db_path) as conn:
        row = conn.execute(
            """
            SELECT MIN(s.interval_start) AS first, MAX(s.interval_end) AS last
            FROM sdat_series s JOIN sdat_documents d ON d.id = s.document_ref WHERE d.sha256 = ?
            """,
            (sha256,),
        ).fetchone()
    if row is None or row["first"] is None:
        return None
    return datetime.fromisoformat(row["first"]), datetime.fromisoformat(row["last"])


def list_documents(db_path: Path, *, limit: int = 50) -> list[SdatDocumentRecord]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM sdat_documents ORDER BY checked_at DESC, id DESC LIMIT ?", (limit,)).fetchall()
    return [_row_to_record(r) for r in rows]


def documents_by_sha(db_path: Path) -> dict[str, SdatDocumentRecord]:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT * FROM sdat_documents").fetchall()
    return {r["sha256"]: _row_to_record(r) for r in rows}
