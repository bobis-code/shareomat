# -*- coding: utf-8 -*-
"""Restore of a backup without Home Assistant (step P0b): proven on the synthetic schema-18 installation.

A backup must come back complete on an empty standalone installation, refuse anything it cannot
vouch for, and never delete or overwrite the data that was there before.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

import shareomat.database.backup as backup_module
from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database import billing
from shareomat.database.backup import BackupError, RestoreError, create_backup, list_backups, restore_backup
from shareomat.database.sqlite import init_db
from shareomat.web.state import get_state
from tests.synthetic.schema18 import content_fingerprint
from tests.test_synthetic_schema18 import FIXTURE

DOCUMENT = b"%PDF-1.4 synthetische unterschriebene Erklaerung\n"
REPO = Path(__file__).resolve().parents[1]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def source(tmp_path) -> Path:
    """A running installation: the synthetic schema-18 database plus one stored declaration."""
    db = tmp_path / "quelle" / "shareomat.db"
    db.parent.mkdir()
    with gzip.open(FIXTURE / "shareomat-schema18.db.gz", "rb") as src, open(db, "wb") as dst:
        shutil.copyfileobj(src, dst)
    documents = db.parent / "documents" / "beitritte"
    documents.mkdir(parents=True)
    (documents / "P02-v1.pdf").write_bytes(DOCUMENT)
    return db


@pytest.fixture
def backup_zip(source, tmp_path) -> Path:
    info = create_backup(source, tmp_path / "sicherungen", documents_dir=source.parent / "documents" / "beitritte",
                         now=datetime(2026, 11, 28, 9, 0, tzinfo=timezone.utc))
    return info.path


def _without_restore_entry(db: Path) -> str:
    """Content fingerprint after removing the one audit row the restore adds (session end times are not compared)."""
    copy = db.with_name("vergleich.db")
    shutil.copyfile(db, copy)
    conn = sqlite3.connect(copy)
    conn.execute("DELETE FROM audit_events WHERE entity = 'backup' AND action = 'restored'")
    conn.commit()
    conn.close()
    try:
        return content_fingerprint(copy)
    finally:
        copy.unlink()


# ── the backup itself passes a restore probe ──


def test_every_backup_passes_the_restore_probe(backup_zip):
    with zipfile.ZipFile(backup_zip) as archive:
        manifest = backup_module._checked_manifest(archive)
    assert {f["path"] for f in manifest["files"]} == {"shareomat.db", "documents/beitritte/P02-v1.pdf"}


def test_a_failing_restore_probe_leaves_no_backup(source, tmp_path, monkeypatch):
    monkeypatch.setattr(backup_module, "_inspect_database", lambda path: ("row 7 missing", 18))
    with pytest.raises(BackupError, match="Wiederherstellungsprobe"):
        create_backup(source, tmp_path / "sicherungen")
    assert list_backups(tmp_path / "sicherungen") == []


# ── restore on an empty standalone installation ──


def test_restore_on_an_empty_installation_brings_everything_back(source, backup_zip, tmp_path):
    target = tmp_path / "neu" / "shareomat.db"
    result = restore_backup(backup_zip, target)
    assert result.moved_aside is None and result.documents == 1 and result.schema_version == 18
    assert _without_restore_entry(target) == content_fingerprint(source)
    assert (target.parent / "documents" / "beitritte" / "P02-v1.pdf").read_bytes() == DOCUMENT
    conn = sqlite3.connect(target)
    try:
        assert conn.execute("SELECT COUNT(*) FROM auth_sessions WHERE revoked_at IS NULL").fetchone()[0] == 0
        assert result.sessions_revoked > 0
        assert conn.execute("SELECT detail FROM audit_events WHERE action = 'restored'").fetchone()[0].startswith(
            "Sicherung vom 2026-11-28")
    finally:
        conn.close()


def test_restored_installation_starts_and_computes_its_released_runs_unchanged(backup_zip, tmp_path):
    target = tmp_path / "neu" / "shareomat.db"
    restore_backup(backup_zip, target)
    init_db(target)                                            # what the first start does
    paths = {n: target.parent / n for n in ("inbox", "archive", "reports", "state")}
    for path in paths.values():
        path.mkdir()
    runtime = RuntimeConfig(paths=PathConfig(**paths))
    get_state().register_db(target)
    get_state().register_runtime(runtime)
    conn = sqlite3.connect(target)
    released = [r[0] for r in conn.execute("SELECT id FROM billing_runs WHERE status = 'released'")]
    conn.execute("UPDATE memberships SET review_note = ''")   # the fixture's open review note blocks every recompute
    conn.commit()
    conn.close()
    assert billing.released_runs_differing(target, runtime, released) == []


def test_command_line_restores_without_home_assistant(backup_zip, tmp_path):
    target = tmp_path / "cli" / "shareomat.db"
    env = {k: v for k, v in os.environ.items() if not k.startswith("SUPERVISOR")}
    env["PYTHONPATH"] = str(REPO)
    done = subprocess.run([sys.executable, "-m", "shareomat.restore", str(backup_zip), "--db", str(target)],
                          capture_output=True, text=True, env=env, cwd=tmp_path, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "Wiederhergestellt" in done.stdout
    assert target.exists()


# ── existing data is never lost ──


def test_existing_data_is_moved_aside_not_deleted(backup_zip, tmp_path):
    target = tmp_path / "bestehend" / "shareomat.db"
    target.parent.mkdir()
    init_db(target)
    old_db = _sha(target)
    old_doc = target.parent / "documents" / "beitritte" / "alt.pdf"
    old_doc.parent.mkdir(parents=True)
    old_doc.write_bytes(b"alt")
    (target.parent / "live.db").write_bytes(b"fluechtig")
    result = restore_backup(backup_zip, target, now=datetime(2026, 11, 28, 10, 0, tzinfo=timezone.utc))
    aside = result.moved_aside
    assert aside == target.parent / "vor-wiederherstellung-20261128T100000Z"
    assert _sha(aside / "shareomat.db") == old_db
    assert (aside / "documents" / "beitritte" / "alt.pdf").read_bytes() == b"alt"
    assert (aside / "live.db").exists() and not (target.parent / "live.db").exists()
    assert not (target.parent / "documents" / "beitritte" / "alt.pdf").exists()


def _rewritten(backup_zip: Path, out: Path, *, db_bytes: bytes | None = None, extra: tuple[str, bytes] | None = None,
               manifest_change=None, drop_manifest: bool = False) -> Path:
    with zipfile.ZipFile(backup_zip) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries["manifest.json"])
    if db_bytes is not None:
        entries["shareomat.db"] = db_bytes
    if manifest_change:
        manifest_change(manifest)
    entries["manifest.json"] = json.dumps(manifest).encode()
    if drop_manifest:
        del entries["manifest.json"]
    if extra:
        entries[extra[0]] = extra[1]
    with zipfile.ZipFile(out, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return out


def _refused(zip_path: Path, target: Path, match: str):
    before = _sha(target) if target.exists() else None
    with pytest.raises(RestoreError, match=match):
        restore_backup(zip_path, target)
    assert (_sha(target) if target.exists() else None) == before
    assert not list(target.parent.glob("vor-wiederherstellung-*"))


@pytest.fixture
def existing(tmp_path) -> Path:
    target = tmp_path / "ziel" / "shareomat.db"
    target.parent.mkdir()
    init_db(target)
    return target


def test_changed_database_in_the_backup_is_refused(backup_zip, existing, tmp_path):
    with zipfile.ZipFile(backup_zip) as archive:
        data = bytearray(archive.read("shareomat.db"))
    data[5000] ^= 0xFF
    bad = _rewritten(backup_zip, tmp_path / "veraendert.zip", db_bytes=bytes(data))
    _refused(bad, existing, "Prüfsumme von shareomat.db")


def test_entry_outside_the_documents_folder_is_refused(backup_zip, existing, tmp_path):
    def add(manifest):
        manifest["files"].append({"path": "../ausserhalb.txt", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest()})
    bad = _rewritten(backup_zip, tmp_path / "pfad.zip", extra=("../ausserhalb.txt", b"x"), manifest_change=add)
    _refused(bad, existing, "Unerwarteter Eintrag")
    assert not (tmp_path / "ausserhalb.txt").exists()


def test_entry_missing_from_the_manifest_is_refused(backup_zip, existing, tmp_path):
    bad = _rewritten(backup_zip, tmp_path / "zusatz.zip", extra=("documents/beitritte/nicht-gelistet.pdf", b"x"))
    _refused(bad, existing, "nicht im Inhaltsverzeichnis")


def test_backup_without_manifest_is_refused(backup_zip, existing, tmp_path):
    _refused(_rewritten(backup_zip, tmp_path / "ohne.zip", drop_manifest=True), existing, "kein Inhaltsverzeichnis")


def test_backup_of_a_newer_schema_is_refused(source, existing, tmp_path):
    newer = tmp_path / "neuer" / "shareomat.db"
    newer.parent.mkdir()
    shutil.copyfile(source, newer)
    conn = sqlite3.connect(newer)
    conn.execute("INSERT INTO schema_migrations (version, applied_at) VALUES (99, '2027-01-01T00:00:00+00:00')")
    conn.commit()
    conn.close()
    info = create_backup(newer, tmp_path / "sicherungen-neu")
    _refused(info.path, existing, "neueren Shareomat-Version")


def test_missing_file_is_refused(existing, tmp_path):
    _refused(tmp_path / "gibt-es-nicht.zip", existing, "nicht gefunden")


def test_database_in_use_is_refused(backup_zip, existing):
    writer = sqlite3.connect(existing)
    writer.execute("BEGIN IMMEDIATE")
    try:
        _refused(backup_zip, existing, "in Gebrauch")
    finally:
        writer.rollback()
        writer.close()
