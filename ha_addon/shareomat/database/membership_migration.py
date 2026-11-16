# -*- coding: utf-8 -*-
"""
File: shareomat/database/membership_migration.py

Purpose:
    Schema-18 migration (ADR-0009): split the per-version participant_contract
    rows of older databases into memberships (join, departure, grid-operator
    report/confirmation) and version rows (declaration, signature, notice,
    consent), each version row linked to exactly one membership.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    * Runs once, before the normal schema statements, only when
      participant_contract still has the old layout (a `joined_at` column).
      init_db() has made the verified backup before this runs.
    * One transaction with foreign_keys OFF (SQLite's documented table
      rebuild). After copying, every kept value of every old row is
      compared with the new row, and every membership with the row it was
      built from; any difference raises, which rolls everything back —
      the add-on then refuses to start instead of running on doubtful data.
    * Grouping: rows of one participant with the same joined_at are one
      membership (publish_version() always carried joined_at unchanged, so
      a different joined_at means a new join after a departure).
    * Old rows could disagree after a later version was published (P1 of
      the 15.11.2026 review). Nothing is silently fixed: the values come
      from the row of the version in force today (what billing used for the
      current days), every other value is written into review_note, and
      shareomat.database.billing blocks billing until the operator confirms.
    * Rows of withdrawn versions are linked to their membership but never
      count for conflicts (they are frozen copies from their publish date).
    * For versions published "with consent of all", the old notified_at
      meant the consent; it is copied to consent_at/consent_evidence (and
      also kept as notice) unless the row is the member's own signature.
"""

from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

from shareomat.database.sqlite import MEMBERSHIPS_DDL, PARTICIPANT_CONTRACT_DDL, now_iso

logger = logging.getLogger(__name__)

_MEMBERSHIP_FIELDS = ("joined_at", "left_at", "exit_reason", "vnb_notified_at", "vnb_confirmed_at",
                      "effective_start_at")
_FIELD_LABELS = {
    "joined_at": "Beitritt", "left_at": "Austritt", "exit_reason": "Austrittsgrund",
    "vnb_notified_at": "beim Netzbetreiber gemeldet", "vnb_confirmed_at": "vom Netzbetreiber bestätigt",
    "effective_start_at": "wirksamer Beginn", "accepted_at": "Unterschrift",
}
# Columns that keep their name and meaning in the new layout (copied 1:1).
_KEPT_COLUMNS = (
    "id", "participant_id", "contract_version_id", "tariff_id", "declaration_issued_at",
    "declaration_snapshot", "declaration_sha256", "accepted_at", "accepted_version", "acceptance_method",
    "signed_at", "signed_by", "signed_document_location", "signed_document_sha256", "paper_original_location",
    "recorded_by", "recorded_at", "notified_at", "notice_evidence", "created_at",
)
_TEXT_DEFAULTS = {"declaration_snapshot", "declaration_sha256", "acceptance_method", "signed_by",
                  "signed_document_location", "signed_document_sha256", "paper_original_location",
                  "recorded_by", "notice_evidence", "exit_reason"}


class MembershipMigrationError(RuntimeError):
    """The schema-18 migration could not prove that it kept every value; nothing was changed."""


def _today() -> date:
    """The day whose contract version decides conflicting values (patched in tests)."""
    return date.today()


# ── Private helpers ─────────────────────────────────────────────────────────


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _value(row: dict, column: str):
    """A column of an old row; older layouts may lack it (then its default)."""
    if column in row:
        return row[column]
    return "" if column in _TEXT_DEFAULTS else None


def _in_force(version: dict, today: str) -> bool:
    return (version["status"] == "published" and (version["valid_from"] or "") <= today
            and (version["valid_until"] is None or version["valid_until"] >= today))


def _authoritative(rows: list[dict], versions: dict[int, dict], today: str) -> dict:
    """The row whose values the membership takes: version in force today, else the newest live version."""
    live = [r for r in rows if versions[r["contract_version_id"]]["status"] != "withdrawn"]
    for row in live:
        if _in_force(versions[row["contract_version_id"]], today):
            return row
    candidates = live or rows
    return max(candidates, key=lambda r: (versions[r["contract_version_id"]]["version"], r["id"]))


def _shown(row: dict, field: str) -> str:
    value = _value(row, field)
    if not value:
        return "leer"
    if field.endswith("_at"):
        try:
            return date.fromisoformat(value).strftime("%d.%m.%Y")
        except ValueError:
            return value
    return value


def _review_note(rows: list[dict], versions: dict[int, dict], chosen: dict) -> str:
    """Describe every value that differs between the live rows of one membership ('' if none)."""
    live = sorted((r for r in rows if versions[r["contract_version_id"]]["status"] != "withdrawn"),
                  key=lambda r: versions[r["contract_version_id"]]["version"])
    findings = []
    for field in _MEMBERSHIP_FIELDS:
        values = {_value(r, field) or "" for r in live}
        if len(values) > 1:
            parts = ", ".join(f"v{versions[r['contract_version_id']]['version']}: {_shown(r, field)}" for r in live)
            findings.append(f"{_FIELD_LABELS[field]} unterschiedlich ({parts}); übernommen: {_shown(chosen, field)}")
    # A signature recorded on an earlier version but missing on a later one: from
    # schema 18 on it counts for the later version too — that changes billing.
    signed_before = None
    for row in live:
        number = versions[row["contract_version_id"]]["version"]
        if _value(row, "accepted_at"):
            signed_before = signed_before or number
        elif signed_before is not None:
            findings.append(f"Unterschrift aus v{signed_before} fehlte bisher in v{number}; gilt jetzt auch dort")
    return "; ".join(findings)


def _group(rows: list[dict]) -> dict[tuple, list[dict]]:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        groups[(row["participant_id"], _value(row, "joined_at"))].append(row)
    return groups


def _consent(row: dict, version: dict) -> tuple:
    """(consent_at, consent_evidence) for a row: the old notified_at meant consent in consent-based versions."""
    if not version.get("consent_based") or not _value(row, "notified_at"):
        return None, ""
    own_signature = _value(row, "accepted_at") and _value(row, "accepted_version") in (None, version["version"])
    if own_signature:
        return None, ""
    return _value(row, "notified_at"), _value(row, "notice_evidence") or ""


def _audit(conn: sqlite3.Connection, detail: str) -> None:
    columns = _columns(conn, "audit_events")
    if not columns:
        return
    if "actor" in columns:
        conn.execute("INSERT INTO audit_events (occurred_at, entity, entity_id, action, detail, actor) "
                     "VALUES (?, 'migration', '18', 'memberships_created', ?, 'system')", (now_iso(), detail))
    else:
        conn.execute("INSERT INTO audit_events (occurred_at, entity, entity_id, action, detail) "
                     "VALUES (?, 'migration', '18', 'memberships_created', ?)", (now_iso(), detail))


def _verify(conn: sqlite3.Connection, old_rows: list[dict], membership_of: dict[int, int],
            source_of: dict[int, dict]) -> None:
    new_rows = {r["id"]: r for r in (dict(x) for x in conn.execute("SELECT * FROM participant_contract"))}
    if len(new_rows) != len(old_rows):
        raise MembershipMigrationError(f"{len(old_rows)} alte, aber {len(new_rows)} neue Zuordnungen")
    for old in old_rows:
        new = new_rows.get(old["id"])
        if new is None or new["membership_id"] != membership_of[old["id"]]:
            raise MembershipMigrationError(f"Zuordnung {old['id']} fehlt oder hat die falsche Mitgliedschaft")
        for column in _KEPT_COLUMNS:
            if new[column] != _value(old, column):
                raise MembershipMigrationError(f"Zuordnung {old['id']}: {column} wurde verändert")
    for membership_id, source in source_of.items():
        stored = dict(conn.execute("SELECT * FROM memberships WHERE id = ?", (membership_id,)).fetchone())
        for field in _MEMBERSHIP_FIELDS:
            if stored[field] != _value(source, field):
                raise MembershipMigrationError(f"Mitgliedschaft {membership_id}: {field} stimmt nicht")
    problems = conn.execute("PRAGMA foreign_key_check").fetchall()
    if problems:
        raise MembershipMigrationError(f"Fremdschlüssel verletzt: {problems[:3]}")


def _migrate(conn: sqlite3.Connection) -> int:
    today = _today().isoformat()
    conn.execute(MEMBERSHIPS_DDL)
    version_columns = _columns(conn, "contract_versions")
    consent_column = "consent_based" if "consent_based" in version_columns else "0 AS consent_based"
    versions = {r["id"]: dict(r) for r in conn.execute(
        f"SELECT id, version, status, valid_from, valid_until, {consent_column} FROM contract_versions")}
    old_rows = [dict(r) for r in conn.execute("SELECT * FROM participant_contract ORDER BY id")]
    now = now_iso()

    membership_of: dict[int, int] = {}
    source_of: dict[int, dict] = {}
    flagged = 0
    for (participant_row_id, _joined), rows in sorted(_group(old_rows).items(), key=lambda kv: min(r["id"] for r in kv[1])):
        chosen = _authoritative(rows, versions, today)
        note = _review_note(rows, versions, chosen)
        flagged += bool(note)
        cursor = conn.execute(
            """
            INSERT INTO memberships (participant_id, joined_at, left_at, exit_reason, vnb_notified_at,
                                     vnb_confirmed_at, effective_start_at, review_note, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (participant_row_id, *(_value(chosen, f) for f in _MEMBERSHIP_FIELDS), note, now, now),
        )
        source_of[cursor.lastrowid] = chosen
        for row in rows:
            membership_of[row["id"]] = cursor.lastrowid

    conn.execute(PARTICIPANT_CONTRACT_DDL.format(table="participant_contract_v18"))
    for row in old_rows:
        consent_at, consent_evidence = _consent(row, versions[row["contract_version_id"]])
        values = [_value(row, c) for c in _KEPT_COLUMNS]
        conn.execute(
            f"INSERT INTO participant_contract_v18 ({', '.join(_KEPT_COLUMNS)}, membership_id, consent_at, "
            f"consent_evidence) VALUES ({', '.join('?' * len(_KEPT_COLUMNS))}, ?, ?, ?)",
            (*values, membership_of[row["id"]], consent_at, consent_evidence),
        )
    conn.execute("DROP TABLE participant_contract")
    conn.execute("ALTER TABLE participant_contract_v18 RENAME TO participant_contract")
    _verify(conn, old_rows, membership_of, source_of)
    _audit(conn, f"{len(source_of)} Mitgliedschaften aus {len(old_rows)} Zuordnungen, {flagged} mit Prüfhinweis")
    return flagged


# ── Public interface ────────────────────────────────────────────────────────


def migrate_to_memberships(db_path: Path) -> None:
    """Rebuild an old participant_contract into memberships + version rows. No-op for the new layout."""
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        if "joined_at" not in _columns(conn, "participant_contract"):
            return
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("BEGIN IMMEDIATE")
        try:
            flagged = _migrate(conn)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        logger.info("Schema 18: memberships created (%d with a review note)", flagged)
    finally:
        conn.close()
