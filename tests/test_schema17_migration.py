# -*- coding: utf-8 -*-
"""Schema 16 -> 17 (SDAT tables): additive, with a verified backup, business data unchanged."""
from __future__ import annotations

import sqlite3

import pytest

from shareomat.database import sqlite as db_module
from shareomat.database.sqlite import init_db
from tests.test_billing_atomicity import case  # noqa: F401  (community, members, meters, tariff, billing draft)


def _as_schema_16(db) -> None:
    """What an installation that never ran schema 17 looks like."""
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE sdat_series")
        conn.execute("DROP TABLE sdat_documents")
        conn.execute("DELETE FROM schema_migrations WHERE version = 17")
        conn.execute("INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (16, '2026-11-08T00:00:00+00:00')")


def _business_content(path) -> dict[str, list[tuple]]:
    with sqlite3.connect(path) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sdat_%' "
            "AND name NOT IN ('schema_migrations', 'sqlite_sequence') ORDER BY name"
        )]
        return {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall() for t in tables}


def test_upgrade_backs_up_first_and_changes_no_business_data(case):  # noqa: F811
    db, runtime, run_id, source = case
    _as_schema_16(db)
    before = _business_content(db)
    assert sum(len(rows) for rows in before.values()) > 0

    init_db(db)

    backups = sorted(db.parent.glob(f"{db.stem}.before-schema-17-from-16-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert _business_content(backups[0]) == before          # the copy restores the old state
    assert _business_content(db) == before                  # the migration changed nothing existing
    with sqlite3.connect(db) as conn:
        assert {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sdat_%'")} >= {
            "sdat_documents", "sdat_series"}
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 17

    init_db(db)   # a normal restart: nothing to migrate, no further copy
    assert len(list(db.parent.glob(f"{db.stem}.before-schema-17-*.db"))) == 1


def test_new_installation_needs_no_backup(tmp_path):
    db = tmp_path / "fresh.db"
    init_db(db)
    assert list(tmp_path.glob("fresh.before-schema-*")) == []


def test_unverifiable_backup_stops_the_migration(case, monkeypatch):  # noqa: F811
    db, runtime, run_id, source = case
    _as_schema_16(db)
    real_connect = sqlite3.connect

    class BrokenCheck:
        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args):
            if sql.startswith("PRAGMA integrity_check"):
                return real_connect(":memory:").execute("SELECT 'database disk image is malformed'")
            return self._conn.execute(sql, *args)

        def close(self):
            self._conn.close()

    def connect(target, *args, **kwargs):
        conn = real_connect(target, *args, **kwargs)
        return BrokenCheck(conn) if "mode=ro" in str(target) and "before-schema" in str(target) else conn

    monkeypatch.setattr(db_module.sqlite3, "connect", connect)
    with pytest.raises(RuntimeError, match="not migrating"):
        init_db(db)
    monkeypatch.undo()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 16
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'sdat_documents'").fetchone() is None
