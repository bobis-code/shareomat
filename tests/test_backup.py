# -*- coding: utf-8 -*-
"""Backups from the settings page (shareomat.database.backup + web route): content, safety, restore."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import shareomat.database.backup as backup_module
from shareomat.database.audit import list_events
from shareomat.database.backup import BackupError, create_backup, find_backup, list_backups
from shareomat.database.community import save_community
from shareomat.database.participants import create_participant, get_participant
from shareomat.database.sqlite import connect, init_db
from shareomat.models.community import Community
from shareomat.models.participant import Participant
from shareomat.security.permissions import required_permission
from tests.test_auth import PASSWORD, Browser, _setup_admin, server  # noqa: F401  (fixture)

NOW = datetime(2026, 11, 15, 10, 30, 0, tzinfo=timezone.utc)
NAME = "shareomat-sicherung-20261115T103000Z.zip"


@pytest.fixture
def installation(tmp_path):
    """A database with data, a stored declaration, and things that must NOT end up in a backup."""
    data = tmp_path / "data"
    data.mkdir()
    db = data / "shareomat.db"
    init_db(db)
    save_community(db, Community(community_id="ZEV-001", name="Test LEG"))
    create_participant(db, Participant("p1", "Haus Müller", email="p1@example.ch"))
    documents = data / "documents" / "beitritte"
    documents.mkdir(parents=True)
    (documents / ("a" * 64 + ".pdf")).write_bytes(b"%PDF-1.4 unterschrieben")
    (documents / ".hidden").write_bytes(b"x")
    (documents / "sub").mkdir()
    (data / "options.json").write_text('{"swisseldex": {"password": "geheim"}}', encoding="utf-8")
    return {"db": db, "documents": documents, "target": tmp_path / "backups", "tmp": tmp_path}


def _dump(db: Path) -> list[str]:
    conn = sqlite3.connect(db)
    try:
        return list(conn.iterdump())
    finally:
        conn.close()


def test_backup_holds_database_documents_and_manifest_only(installation):
    info = create_backup(installation["db"], installation["target"], documents_dir=installation["documents"], now=NOW)
    assert info.name == NAME and info.path.is_file()
    with zipfile.ZipFile(info.path) as archive:
        names = set(archive.namelist())
        assert names == {"shareomat.db", "manifest.json", "documents/beitritte/" + "a" * 64 + ".pdf"}
        manifest = json.loads(archive.read("manifest.json"))
        for item in manifest["files"]:
            assert hashlib.sha256(archive.read(item["path"])).hexdigest() == item["sha256"]
        assert b"geheim" not in b"".join(archive.read(n) for n in names)
    assert manifest["schema_version"] >= 17 and manifest["created_at"] == NOW.isoformat()


def test_backup_restores_to_identical_data(installation):
    info = create_backup(installation["db"], installation["target"], documents_dir=installation["documents"], now=NOW)
    restored = installation["tmp"] / "restored"
    restored.mkdir()
    with zipfile.ZipFile(info.path) as archive:
        archive.extract("shareomat.db", restored)
    copy = restored / "shareomat.db"
    assert _dump(copy) == _dump(installation["db"])
    init_db(copy)                       # opens as a normal installation, no migration needed
    assert get_participant(copy, "p1").label == "Haus Müller"
    assert not list(restored.glob("*.before-schema-*"))


def test_backup_only_reads_the_source(installation):
    before = _dump(installation["db"])
    conn = sqlite3.connect(installation["db"])
    version = conn.execute("PRAGMA data_version").fetchone()[0]
    create_backup(installation["db"], installation["target"], documents_dir=installation["documents"], now=NOW)
    assert conn.execute("PRAGMA data_version").fetchone()[0] == version   # a running release is not disturbed
    conn.close()
    assert _dump(installation["db"]) == before


def test_backup_includes_uncommitted_wal_content_consistently(installation):
    with connect(installation["db"]) as conn:
        with conn:
            conn.execute("UPDATE participants SET label = 'Haus Neu' WHERE participant_id = 'p1'")
    info = create_backup(installation["db"], installation["target"], now=NOW)
    restored = installation["tmp"] / "r"
    with zipfile.ZipFile(info.path) as archive:
        archive.extract("shareomat.db", restored)
    assert get_participant(restored / "shareomat.db", "p1").label == "Haus Neu"


def test_broken_database_leaves_nothing_behind(installation):
    broken = installation["tmp"] / "broken.db"
    broken.write_bytes(b"das ist keine Datenbank " * 200)
    with pytest.raises(BackupError):
        create_backup(broken, installation["target"], now=NOW)
    assert list(installation["target"].iterdir()) == []


def test_failed_verification_leaves_nothing_behind(installation, monkeypatch):
    def fail(*_args):
        raise BackupError("Prüfsumme stimmt nicht.")
    monkeypatch.setattr(backup_module, "_verify_zip", fail)
    with pytest.raises(BackupError):
        create_backup(installation["db"], installation["target"], now=NOW)
    assert list(installation["target"].iterdir()) == []


def test_second_backup_in_the_same_second_never_overwrites(installation):
    first = create_backup(installation["db"], installation["target"], now=NOW)
    digest = hashlib.sha256(first.path.read_bytes()).hexdigest()
    with pytest.raises(BackupError):
        create_backup(installation["db"], installation["target"], now=NOW)
    assert hashlib.sha256(first.path.read_bytes()).hexdigest() == digest
    assert [b.name for b in list_backups(installation["target"])] == [NAME]


def test_missing_database_is_refused(installation):
    with pytest.raises(BackupError):
        create_backup(installation["tmp"] / "fehlt.db", installation["target"], now=NOW)


def test_listing_and_lookup_accept_only_generated_names(installation):
    create_backup(installation["db"], installation["target"], now=NOW)
    create_backup(installation["db"], installation["target"], now=NOW + timedelta(hours=1))
    (installation["target"] / "fremd.zip").write_bytes(b"x")
    (installation["target"] / "shareomat-sicherung-kaputt.zip").write_bytes(b"x")
    assert [b.name for b in list_backups(installation["target"])] == [
        "shareomat-sicherung-20261115T113000Z.zip", NAME]
    assert find_backup(installation["target"], NAME).name == NAME
    for bad in ("../shareomat.db", "..", "fremd.zip", "/etc/passwd", NAME + "/..", "..\\" + NAME,
                "shareomat-sicherung-20261115T103000Z.zip\x00", "", None, 5):
        assert find_backup(installation["target"], bad) is None
    assert find_backup(installation["target"], "shareomat-sicherung-20991231T000000Z.zip") is None


def test_a_link_with_a_backup_name_is_not_served(installation):
    target = installation["target"]
    target.mkdir()
    link = target / NAME
    try:
        os.symlink(installation["db"], link)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links not available here")
    assert find_backup(target, NAME) is None and list_backups(target) == []


def test_backups_need_their_own_permission_not_just_view():
    assert required_permission("GET", "settings", ["backup", NAME]) == "backup.manage"
    assert required_permission("POST", "settings", ["backup"]) == "backup.manage"
    assert required_permission("POST", "settings", ["backup", "confirm"]) == "backup.manage"
    assert required_permission("GET", "settings", []) == "view"
    assert required_permission("POST", "settings", []) == "system.configure"


# ── through the real web server, with login ────────────────────────────────


def _backups(srv):
    return list_backups(srv["runtime"].paths.state / "backups")


def _age_password_entry(srv):
    old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    with connect(srv["db"]) as conn:
        with conn:
            conn.execute("UPDATE auth_sessions SET auth_at = ?", (old,))


def test_create_and_download_through_the_settings_page(server):  # noqa: F811
    a = _setup_admin(server)
    status, _, html = a.get("/settings")
    assert status == 200 and "Sicherung erstellen" in html and "Noch keine Sicherung" in html
    status, headers, _ = a.post("/settings/backup", {"csrf_token": a.page_csrf("/settings")})
    assert status == 303
    [info] = _backups(server)
    assert [e.action for e in list_events(server["db"], entity="backup")] == ["created"]

    _, _, html = a.get("/settings")
    assert info.name in html and "Herunterladen" in html
    status, headers, body = a.request("GET", f"/settings/backup/{info.name}")
    assert status == 200 and headers["Content-Type"] == "application/zip"
    assert headers["Cache-Control"] == "no-store" and int(headers["Content-Length"]) == info.size
    events = list_events(server["db"], entity="backup")
    assert events[0].action == "downloaded" and events[0].actor == "user:1:admin"


def test_download_paths_outside_the_backup_folder_are_404(server):  # noqa: F811
    a = _setup_admin(server)
    for path in ("/settings/backup/shareomat.db", "/settings/backup/..%2F..%2Fshareomat.db",
                 "/settings/backup/..%2Fstate%2Fx", "/settings/backup/%2E%2E"):
        assert a.get(path)[0] == 404, path


def test_without_login_nothing_is_created_or_served(server):  # noqa: F811
    _setup_admin(server)
    stranger = Browser(server["port"])
    assert stranger.post("/settings/backup", {"csrf_token": "x"})[1]["Location"].startswith("/login")
    assert _backups(server) == []
    assert stranger.get(f"/settings/backup/{NAME}")[1]["Location"].startswith("/login")


def test_an_old_password_entry_must_be_confirmed_first(server):  # noqa: F811
    a = _setup_admin(server)
    a.post("/settings/backup", {"csrf_token": a.page_csrf("/settings")})
    [info] = _backups(server)
    _age_password_entry(server)

    status, headers, _ = a.get(f"/settings/backup/{info.name}")
    assert status == 303 and headers["Location"] == "/settings"
    _, _, html = a.get("/settings")
    assert "Zum Herunterladen einer Sicherung bitte zuerst Ihr Passwort bestätigen" in html
    assert 'name="confirm_password"' in html and f"/settings/backup/{info.name}" not in html

    a.post("/settings/backup/confirm", {"csrf_token": a.page_csrf("/settings"), "confirm_password": "falsch"})
    assert a.get(f"/settings/backup/{info.name}")[0] == 303
    a.post("/settings/backup/confirm", {"csrf_token": a.page_csrf("/settings"), "confirm_password": PASSWORD})
    assert a.get(f"/settings/backup/{info.name}")[0] == 200


def test_creating_with_an_old_password_entry_needs_the_password(server):  # noqa: F811
    a = _setup_admin(server)
    _age_password_entry(server)
    a.post("/settings/backup", {"csrf_token": a.page_csrf("/settings")})
    assert _backups(server) == []
    _, _, html = a.get("/settings")
    assert re.search(r"Passwort eingeben \(Sicherungen enthalten alle Daten\)", html)
    a.post("/settings/backup", {"csrf_token": a.page_csrf("/settings"), "confirm_password": PASSWORD})
    assert len(_backups(server)) == 1
