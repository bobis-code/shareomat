# -*- coding: utf-8 -*-
"""Schema 17 -> 18 (memberships, ADR-0009) against the reference recorded with the 0.6.12 code.

See tests/fixtures/membership_migration/README.md for how the reference was made.
"""
from __future__ import annotations

import gzip
import json
import shutil
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pytest

import shareomat.database.membership_migration as migration
from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.billing import _result_fingerprint, compute_billing_preview, get_billing_run
from shareomat.database.billing_eligibility import EligibilityResolver
from shareomat.database.memberships import confirm_review, memberships_needing_review
from shareomat.database.sqlite import init_db

FIXTURES = Path(__file__).parent / "fixtures" / "membership_migration"
UNCHANGED_TABLES_EXCLUDED = {"participant_contract", "memberships", "schema_migrations", "audit_events",
                             "sqlite_sequence"}


@pytest.fixture(autouse=True)
def _migration_day(monkeypatch):
    monkeypatch.setattr(migration, "_today", lambda: date(2026, 11, 16))


def _install(tmp_path, scenario: str) -> tuple[Path, dict, RuntimeConfig]:
    db = tmp_path / "shareomat.db"
    with gzip.open(FIXTURES / f"v17_{scenario}.db.gz", "rb") as src, db.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    with gzip.open(FIXTURES / f"expected_{scenario}.json.gz", "rt", encoding="utf-8") as f:
        expected = json.load(f)
    for name in ("archive", "reports", "state"):
        (tmp_path / name).mkdir()
    runtime = RuntimeConfig(paths=PathConfig(inbox=FIXTURES / "inbox", archive=tmp_path / "archive",
                                             reports=tmp_path / "reports", state=tmp_path / "state"))
    return db, expected, runtime


def _content(db: Path, columns: dict | None = None) -> dict:
    conn = sqlite3.connect(db)
    try:
        result = {}
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
            names = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            if columns is not None and table in columns:
                names = [n for n in names if n in columns[table]]
            result[table] = sorted(map(tuple, conn.execute(f"SELECT {', '.join(names)} FROM {table}")))
        return result
    finally:
        conn.close()


def _columns(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        return {t: [r[1] for r in conn.execute(f"PRAGMA table_info({t})")] for t in tables}
    finally:
        conn.close()


def _eligibility(db: Path) -> dict:
    resolver = EligibilityResolver(db)
    result, day = {}, date(2025, 12, 1)
    while day <= date(2027, 11, 30):
        e = resolver.for_day(day)
        result[day.isoformat()] = {"meters": e.meters, "excluded": e.excluded,
                                   "unconfirmed": sorted(e.unconfirmed_participants)}
        day += timedelta(days=1)
    return result


def _owner(db: Path) -> dict:
    conn = sqlite3.connect(db)
    try:
        return dict(conn.execute(
            "SELECT m.meter_id, p.participant_id FROM meters m JOIN participants p ON p.id = m.participant_id"))
    finally:
        conn.close()


def _participants_differing(expected: dict, actual: dict, owner: dict) -> set[str]:
    differing = set()
    for day, exp in expected.items():
        act = actual[day]
        for key in ("meters", "excluded"):
            for meter in set(exp[key]) | set(act[key]):
                if exp[key].get(meter) != act[key].get(meter):
                    differing.add(owner[meter])
        differing |= set(exp["unconfirmed"]) ^ set(act["unconfirmed"])
    return differing


def _month(start: str) -> tuple[date, date]:
    first = date.fromisoformat(start)
    last = (first.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    return first, last


# ── A1-A4 with the clean reference: everything identical ───────────────────


def test_clean_reference_keeps_every_billing_decision(tmp_path):
    db, expected, runtime = _install(tmp_path, "clean")
    before, old_columns = _content(db), _columns(db)

    init_db(db)

    after = _content(db, old_columns)
    for table in before:
        if table not in UNCHANGED_TABLES_EXCLUDED:
            assert after[table] == before[table], table
    assert after["audit_events"][:len(before["audit_events"])] == before["audit_events"]   # only appended
    [backup] = tmp_path.glob("shareomat.before-schema-18-from-17-*.db")
    assert _content(backup) == before
    assert memberships_needing_review(db) == []
    assert _eligibility(db) == expected["eligibility"]                                   # A3: every day
    for start, info in expected["previews"].items():                                     # A4
        preview = compute_billing_preview(db, runtime, *_month(start), persist_readings=False)
        assert _result_fingerprint(preview) == info["fingerprint"], start
    run = get_billing_run(db, expected["released_run"]["id"]).run
    assert run.status == "released" and run.result_fingerprint == expected["released_run"]["fingerprint"]


def test_every_old_value_survives_the_rebuild(tmp_path):
    db, _expected, _runtime = _install(tmp_path, "clean")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    old = {r["id"]: dict(r) for r in conn.execute("SELECT * FROM participant_contract")}
    conn.close()

    init_db(db)

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    new = {r["id"]: dict(r) for r in conn.execute(
        "SELECT pc.*, m.joined_at AS m_joined_at, m.left_at AS m_left_at, m.exit_reason AS m_exit_reason, "
        "m.vnb_notified_at AS m_vnb_notified_at, m.vnb_confirmed_at AS m_vnb_confirmed_at, "
        "m.effective_start_at AS m_effective_start_at FROM participant_contract pc "
        "JOIN memberships m ON m.id = pc.membership_id")}
    assert "joined_at" not in new[next(iter(new))]
    conn.close()
    assert new.keys() == old.keys()
    for row_id, row in old.items():
        for column, value in row.items():
            if column in ("joined_at", "left_at", "exit_reason", "vnb_notified_at", "vnb_confirmed_at",
                          "effective_start_at"):
                assert new[row_id]["m_" + column] == value, (row_id, column)   # moved to the membership
            elif column != "effective_end_at":                                 # was always equal to left_at
                assert new[row_id][column] == value, (row_id, column)
        assert row["effective_end_at"] == row["left_at"]


# ── conflicting old data: flagged, billing blocked, nothing silently fixed ──


def test_conflicts_are_flagged_and_block_billing_until_confirmed(tmp_path):
    db, expected, runtime = _install(tmp_path, "conflict")
    init_db(db)

    review = {m.participant_id: m for m in memberships_needing_review(db)}
    assert set(review) == {"c_pending", "c_unsigned"}
    assert "Austritt unterschiedlich (v1: 31.12.2026, v2: leer, v3: leer); übernommen: leer" in review["c_pending"].review_note
    assert "Unterschrift aus v1 fehlte bisher in v2" in review["c_unsigned"].review_note
    differing = _participants_differing(expected["eligibility"], _eligibility(db), _owner(db))
    assert differing == {"c_pending", "c_unsigned"}                      # only where the old data disagreed

    for start in expected["previews"]:
        preview = compute_billing_preview(db, runtime, *_month(start), persist_readings=False)
        blockers = [h for h in preview.availability.blocking_issues if "ist zu prüfen" in h]
        assert len(blockers) == 2, start                                  # no release while unclear
    run = get_billing_run(db, expected["released_run"]["id"]).run
    assert run.result_fingerprint == expected["released_run"]["fingerprint"]   # released run untouched

    for membership in review.values():
        confirm_review(db, membership.id)
    assert memberships_needing_review(db) == []
    preview = compute_billing_preview(db, runtime, *_month("2026-11-01"), persist_readings=False)
    assert not [h for h in preview.availability.blocking_issues if "ist zu prüfen" in h]


def test_failed_verification_changes_nothing(tmp_path, monkeypatch):
    db, _expected, _runtime = _install(tmp_path, "clean")
    before = _content(db)

    def broken(*_args):
        raise migration.MembershipMigrationError("Prüfung fehlgeschlagen")
    monkeypatch.setattr(migration, "_verify", broken)
    with pytest.raises(migration.MembershipMigrationError):
        init_db(db)
    assert _content(db) == before                       # rolled back: old layout, old data
    assert "joined_at" in _columns(db)["participant_contract"]


def test_restart_after_migration_does_nothing(tmp_path):
    db, _expected, _runtime = _install(tmp_path, "clean")
    init_db(db)
    once = _content(db)
    init_db(db)
    assert _content(db) == once
    assert len(list(tmp_path.glob("shareomat.before-schema-*"))) == 1


def test_new_installation_starts_with_the_new_layout(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    columns = _columns(db)
    assert "membership_id" in columns["participant_contract"] and "joined_at" not in columns["participant_contract"]
    assert "effective_start_at" in columns["memberships"]
