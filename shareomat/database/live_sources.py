# -*- coding: utf-8 -*-
"""
File: shareomat/database/live_sources.py

Purpose:
    Read and write the live source assignment (ADR-0007, Abschnitt 2).
    "At most one active live source per official meter" is enforced here in
    one transaction and, independently of this code, by partial unique
    indexes in the database.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Lifecycle: testing (received, checked, shown, never counted) -> active
    (counted) -> retired (values dropped, row kept for traceability).
    A switch of sources is one transaction: there is no moment with two
    active sources. Every change is written to the audit log in the same
    transaction as the change itself.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from datetime import datetime
from pathlib import Path

from shareomat.database.audit import record_event
from shareomat.database.sqlite import connect, now_iso
from shareomat.leg_const import METER_ROLE_CONSUMER, METER_ROLE_GRID
from shareomat.models.live_source import (
    ADAPTER_EMSOMAT_LOCAL,
    ADAPTER_GATEWAY_MQTT,
    ADAPTER_HA_MQTT,
    ADAPTER_SOURCE_TYPES,
    ADAPTER_WAN_SPARKPLUG,
    EVIDENCE_LEVELS,
    IDENTITY_BROKER_ACL,
    MODE_IMPORT_EXPORT,
    MODE_IMPORT_ONLY,
    MODE_SIGNED,
    SIGN_POSITIVE_IS_EXPORT,
    SIGN_POSITIVE_IS_IMPORT,
    STATE_ACTIVE,
    STATE_RETIRED,
    STATE_TESTING,
    LiveSource,
)

MAX_POWER_LIMIT_W = 1_000_000.0
_MAX_NOTE_LEN = 300
# MQTT topic levels: no separators or wildcards, no whitespace or control characters.
_ID_RE = re.compile(r"^[^/+#\s\x00-\x1f\x7f]{1,64}$")
_ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_METRIC_IMPORT = "Market/ImportPowerNow"
_METRIC_EXPORT = "Market/ExportPowerNow"

_SELECT = """
    SELECT s.*, m.meter_id AS meter_business_id, p.participant_id AS participant_business_id
    FROM live_sources s
    JOIN meters m ON m.id = s.meter_id
    LEFT JOIN participants p ON p.id = m.participant_id
"""


class LiveSourceError(ValueError):
    """A live source operation was refused; the message is shown to the administrator."""


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _row_to_source(row: sqlite3.Row) -> LiveSource:
    return LiveSource(
        id=row["id"],
        meter_row_id=row["meter_id"],
        meter_id=row["meter_business_id"],
        participant_id=row["participant_business_id"] or "",
        source_type=row["source_type"],
        adapter=row["adapter"],
        source_key=row["source_key"],
        measurement_mode=row["measurement_mode"],
        max_power_w=float(row["max_power_w"]),
        freshness_evidence=row["freshness_evidence"],
        state=row["state"],
        sign_convention=row["sign_convention"],
        no_export_note=row["no_export_note"],
        adapter_config=json.loads(row["adapter_config"] or "{}"),
        identity_basis=row["identity_basis"],
        identity_note=row["identity_note"],
        identity_verified_at=_ts(row["identity_verified_at"]),
        activated_at=_ts(row["activated_at"]),
        retired_at=_ts(row["retired_at"]),
        created_at=_ts(row["created_at"]),
        updated_at=_ts(row["updated_at"]),
    )


def list_live_sources(db_path: Path, *, include_retired: bool = False) -> list[LiveSource]:
    """All registered sources, oldest first."""
    query = _SELECT
    if not include_retired:
        query += f" WHERE s.state != '{STATE_RETIRED}'"
    query += " ORDER BY s.id"
    with connect(db_path) as conn:
        return [_row_to_source(r) for r in conn.execute(query).fetchall()]


def get_live_source(db_path: Path, source_id: int) -> LiveSource | None:
    with connect(db_path) as conn:
        row = conn.execute(_SELECT + " WHERE s.id = ?", (source_id,)).fetchone()
        return _row_to_source(row) if row else None


def _validated_id(value: str, label: str) -> str:
    value = (value or "").strip()
    if not _ID_RE.match(value):
        raise LiveSourceError(f"{label}: ungültige Kennung (1–64 Zeichen, ohne Leerzeichen und ohne / + #).")
    return value


def build_source_key(adapter: str, edge_node_id: str = "", device_id: str = "") -> str:
    """The canonical technical identity of a source, unique per adapter."""
    if adapter == ADAPTER_EMSOMAT_LOCAL:
        return f"{adapter}:{_validated_id(edge_node_id, 'Sparkplug-Knoten')}"
    if adapter == ADAPTER_WAN_SPARKPLUG:
        edge = _validated_id(edge_node_id, "Standort (edge_node_id)")
        return f"{adapter}:{edge}/{_validated_id(device_id, 'Gerät (device_id)')}"
    if adapter in (ADAPTER_HA_MQTT, ADAPTER_GATEWAY_MQTT):
        return f"{adapter}:{_validated_id(device_id, 'Sender-/Geräte-Kennung')}"
    raise LiveSourceError("Unbekannte Art der Live-Messquelle.")


def _validated_max_power(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LiveSourceError("Maximale Leistung: keine gültige Zahl.") from None
    if not math.isfinite(number) or number <= 0 or number > MAX_POWER_LIMIT_W:
        raise LiveSourceError("Maximale Leistung muss grösser als 0 und höchstens 1'000'000 W sein.")
    return number


def _validated_note(value: str) -> str:
    value = (value or "").strip()
    if len(value) > _MAX_NOTE_LEN:
        raise LiveSourceError(f"Bemerkung ist zu lang (höchstens {_MAX_NOTE_LEN} Zeichen).")
    return value


def _validated_mode(mode: str, sign_convention: str | None, no_export_note: str, meter_role: str) -> tuple[str | None, str]:
    note = _validated_note(no_export_note)
    if mode == MODE_IMPORT_EXPORT:
        return None, ""
    if mode == MODE_SIGNED:
        if sign_convention not in (SIGN_POSITIVE_IS_IMPORT, SIGN_POSITIVE_IS_EXPORT):
            raise LiveSourceError("Bei einem Wert mit Vorzeichen muss die Vorzeichenregel gewählt sein.")
        return sign_convention, ""
    if mode == MODE_IMPORT_ONLY:
        if meter_role != METER_ROLE_CONSUMER:
            raise LiveSourceError("«Nur Bezug» ist nur bei einem reinen Verbraucher-Messpunkt zulässig.")
        if not note:
            raise LiveSourceError("Bei «Nur Bezug» bitte festhalten, warum Einspeisung ausgeschlossen ist.")
        return None, note
    raise LiveSourceError("Unbekannte Messart.")


def _channels(adapter: str, source_key: str, mode: str, config: dict) -> list[tuple[str, str]]:
    """(quantity, channel_ref): what this source reads. Used to keep one channel out of two active sources."""
    if adapter == ADAPTER_HA_MQTT:
        wanted = {"import": config.get("import_entity"), "export": config.get("export_entity"),
                  "signed": config.get("signed_entity")}
        return [(q, f"ha:{ref}") for q, ref in wanted.items() if ref]
    if mode == MODE_SIGNED:
        return [("signed", f"{source_key}#signed")]
    channels = [("import", f"{source_key}#{_METRIC_IMPORT}")]
    if mode == MODE_IMPORT_EXPORT:
        channels.append(("export", f"{source_key}#{_METRIC_EXPORT}"))
    return channels


def _validated_config(adapter: str, mode: str, config: dict | None) -> dict:
    config = dict(config or {})
    if adapter != ADAPTER_HA_MQTT:
        if config:
            raise LiveSourceError("Für diese Art der Live-Messquelle sind keine Zusatzangaben vorgesehen.")
        return {}
    allowed = {"import_entity", "export_entity", "signed_entity", "evidence_entity"}
    cleaned = {k: str(v).strip() for k, v in config.items() if k in allowed and str(v).strip()}
    for key, value in cleaned.items():
        if not _ENTITY_RE.match(value):
            raise LiveSourceError(f"Ungültige Entity-ID: {value}")
    needed = {MODE_IMPORT_EXPORT: ("import_entity", "export_entity"), MODE_IMPORT_ONLY: ("import_entity",),
              MODE_SIGNED: ("signed_entity",)}[mode]
    for key in needed:
        if key not in cleaned:
            raise LiveSourceError("Bitte die benötigten Entity-IDs angeben.")
    return cleaned


def _meter_row(conn: sqlite3.Connection, meter_id: str) -> sqlite3.Row:
    row = conn.execute("SELECT id, role FROM meters WHERE meter_id = ?", (meter_id,)).fetchone()
    if row is None:
        raise LiveSourceError(f"Messpunkt '{meter_id}' wurde nicht gefunden.")
    if row["role"] == METER_ROLE_GRID:
        raise LiveSourceError("Einem Netz-Messpunkt kann keine Live-Messquelle zugeordnet werden.")
    return row


def _source_row(conn: sqlite3.Connection, source_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM live_sources WHERE id = ?", (source_id,)).fetchone()
    if row is None:
        raise LiveSourceError("Die Live-Messquelle wurde nicht gefunden.")
    return row


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _check_wan_device_free(conn: sqlite3.Connection, source_key: str, *, exclude_id: int | None = None) -> None:
    """The local relay and Emsomat know a remote participant by device_id alone:
    two sites with the same device_id would overwrite each other (ADR-0007 S-L4)."""
    device_id = source_key.rsplit("/", 1)[-1]
    rows = conn.execute(
        "SELECT id, source_key FROM live_sources WHERE adapter = ? AND state IN (?, ?)",
        (ADAPTER_WAN_SPARKPLUG, STATE_TESTING, STATE_ACTIVE),
    ).fetchall()
    for row in rows:
        if row["id"] != exclude_id and row["source_key"].rsplit("/", 1)[-1] == device_id:
            raise LiveSourceError(f"Die Geräte-Kennung '{device_id}' ist bereits einem anderen Standort zugeordnet.")


def create_live_source(
    db_path: Path,
    *,
    meter_id: str,
    adapter: str,
    edge_node_id: str = "",
    device_id: str = "",
    measurement_mode: str = MODE_IMPORT_EXPORT,
    sign_convention: str | None = None,
    no_export_note: str = "",
    adapter_config: dict | None = None,
    max_power_w: float,
    freshness_evidence: str = "C",
) -> int:
    """Register a source for an official meter in state `testing`. Returns its id."""
    if adapter not in ADAPTER_SOURCE_TYPES:
        raise LiveSourceError("Unbekannte Art der Live-Messquelle.")
    if freshness_evidence not in EVIDENCE_LEVELS:
        raise LiveSourceError("Unbekannte Nachweisstufe.")
    source_key = build_source_key(adapter, edge_node_id, device_id)
    power = _validated_max_power(max_power_w)
    now = now_iso()
    with connect(db_path) as conn:
        try:
            _begin(conn)
            meter = _meter_row(conn, meter_id)
            sign, note = _validated_mode(measurement_mode, sign_convention, no_export_note, meter["role"])
            config = _validated_config(adapter, measurement_mode, adapter_config)
            if adapter == ADAPTER_WAN_SPARKPLUG:
                _check_wan_device_free(conn, source_key)
            cursor = conn.execute(
                """
                INSERT INTO live_sources (meter_id, source_type, adapter, source_key, measurement_mode, sign_convention,
                    no_export_note, adapter_config, max_power_w, freshness_evidence, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (meter["id"], ADAPTER_SOURCE_TYPES[adapter], adapter, source_key, measurement_mode, sign, note,
                 json.dumps(config, sort_keys=True), power, freshness_evidence, STATE_TESTING, now, now),
            )
            source_id = int(cursor.lastrowid)
            for quantity, channel_ref in _channels(adapter, source_key, measurement_mode, config):
                conn.execute(
                    "INSERT INTO live_source_channels (live_source_id, quantity, channel_ref) VALUES (?, ?, ?)",
                    (source_id, quantity, channel_ref),
                )
            record_event(conn, "live_source", source_id, "assigned", f"{meter_id}: {source_key} ({measurement_mode})")
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise LiveSourceError("Diese Quelle ist bereits einem Messpunkt zugeordnet.") from exc
        except Exception:
            conn.rollback()
            raise
    return source_id


def update_live_source(
    db_path: Path,
    source_id: int,
    *,
    max_power_w: float,
    freshness_evidence: str,
    measurement_mode: str,
    sign_convention: str | None = None,
    no_export_note: str = "",
) -> None:
    """Change the settings of a source. The identity (adapter, key) cannot be
    changed: a different technical source is a new source."""
    if freshness_evidence not in EVIDENCE_LEVELS:
        raise LiveSourceError("Unbekannte Nachweisstufe.")
    power = _validated_max_power(max_power_w)
    with connect(db_path) as conn:
        try:
            _begin(conn)
            row = _source_row(conn, source_id)
            if row["state"] == STATE_RETIRED:
                raise LiveSourceError("Eine abgelöste Live-Messquelle kann nicht mehr geändert werden.")
            meter = conn.execute("SELECT role FROM meters WHERE id = ?", (row["meter_id"],)).fetchone()
            sign, note = _validated_mode(measurement_mode, sign_convention, no_export_note, meter["role"])
            config = _validated_config(row["adapter"], measurement_mode, json.loads(row["adapter_config"] or "{}"))
            conn.execute(
                """
                UPDATE live_sources SET max_power_w = ?, freshness_evidence = ?, measurement_mode = ?,
                    sign_convention = ?, no_export_note = ?, updated_at = ? WHERE id = ?
                """,
                (power, freshness_evidence, measurement_mode, sign, note, now_iso(), source_id),
            )
            conn.execute("DELETE FROM live_source_channels WHERE live_source_id = ?", (source_id,))
            for quantity, channel_ref in _channels(row["adapter"], row["source_key"], measurement_mode, config):
                conn.execute(
                    "INSERT INTO live_source_channels (live_source_id, quantity, channel_ref) VALUES (?, ?, ?)",
                    (source_id, quantity, channel_ref),
                )
            record_event(conn, "live_source", source_id, "updated",
                         f"max_power_w={power}, evidence={freshness_evidence}, mode={measurement_mode}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def activate_live_source(db_path: Path, source_id: int) -> int | None:
    """Make a `testing` source the active one of its meter. An already active
    source of that meter is retired in the same transaction. Returns its id."""
    now = now_iso()
    with connect(db_path) as conn:
        try:
            _begin(conn)
            row = _source_row(conn, source_id)
            if row["state"] != STATE_TESTING:
                raise LiveSourceError("Nur eine Live-Messquelle in Prüfung kann aktiviert werden.")
            meter = conn.execute("SELECT role, meter_id FROM meters WHERE id = ?", (row["meter_id"],)).fetchone()
            if meter["role"] == METER_ROLE_GRID:
                raise LiveSourceError("Einem Netz-Messpunkt kann keine Live-Messquelle zugeordnet werden.")
            previous = conn.execute(
                "SELECT id FROM live_sources WHERE meter_id = ? AND state = ?", (row["meter_id"], STATE_ACTIVE)
            ).fetchone()
            replaced = previous["id"] if previous else None
            if replaced is not None:
                conn.execute(
                    "UPDATE live_sources SET state = ?, retired_at = ?, updated_at = ? WHERE id = ?",
                    (STATE_RETIRED, now, now, replaced),
                )
                record_event(conn, "live_source", replaced, "retired", f"ersetzt durch Quelle {source_id}")
            taken = conn.execute(
                """
                SELECT c.channel_ref FROM live_source_channels c
                JOIN live_source_channels mine ON mine.channel_ref = c.channel_ref AND mine.live_source_id = ?
                JOIN live_sources s ON s.id = c.live_source_id
                WHERE s.state = ? AND s.id != ?
                """,
                (source_id, STATE_ACTIVE, source_id),
            ).fetchone()
            if taken is not None:
                raise LiveSourceError(f"Der Kanal {taken['channel_ref']} wird bereits von einer aktiven Quelle verwendet.")
            conn.execute(
                "UPDATE live_sources SET state = ?, activated_at = ?, updated_at = ? WHERE id = ?",
                (STATE_ACTIVE, now, now, source_id),
            )
            record_event(conn, "live_source", source_id, "activated", f"{meter['meter_id']}: {row['source_key']}")
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise LiveSourceError("Pro Messpunkt ist höchstens eine aktive Live-Messquelle erlaubt.") from exc
        except Exception:
            conn.rollback()
            raise
    return replaced


def retire_live_source(db_path: Path, source_id: int) -> None:
    """Deactivate a source. Its values are dropped from now on, the row stays."""
    now = now_iso()
    with connect(db_path) as conn:
        try:
            _begin(conn)
            row = _source_row(conn, source_id)
            if row["state"] == STATE_RETIRED:
                raise LiveSourceError("Die Live-Messquelle ist bereits abgelöst.")
            conn.execute(
                "UPDATE live_sources SET state = ?, retired_at = ?, updated_at = ? WHERE id = ?",
                (STATE_RETIRED, now, now, source_id),
            )
            record_event(conn, "live_source", source_id, "retired", row["source_key"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def record_identity_proof(db_path: Path, source_id: int, *, note: str) -> None:
    """Record that the broker binding of this source was proven by a negative test."""
    note = _validated_note(note)
    if not note:
        raise LiveSourceError("Bitte festhalten, womit die Identität nachgewiesen wurde.")
    now = now_iso()
    with connect(db_path) as conn:
        try:
            _begin(conn)
            row = _source_row(conn, source_id)
            if row["state"] == STATE_RETIRED:
                raise LiveSourceError("Die Live-Messquelle ist abgelöst.")
            conn.execute(
                "UPDATE live_sources SET identity_basis = ?, identity_note = ?, identity_verified_at = ?, updated_at = ? WHERE id = ?",
                (IDENTITY_BROKER_ACL, note, now, now, source_id),
            )
            record_event(conn, "live_source", source_id, "identity_recorded", note)
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def clear_identity_proof(db_path: Path, source_id: int) -> None:
    """Withdraw the proof, e.g. after a change of broker, ACL or logins."""
    now = now_iso()
    with connect(db_path) as conn:
        try:
            _begin(conn)
            _source_row(conn, source_id)
            conn.execute(
                "UPDATE live_sources SET identity_basis = '', identity_note = '', identity_verified_at = NULL, updated_at = ? WHERE id = ?",
                (now, source_id),
            )
            record_event(conn, "live_source", source_id, "identity_cleared", "")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
