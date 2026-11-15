# -*- coding: utf-8 -*-
"""
File: shareomat/database/backup.py

Purpose:
    Operator-triggered backup ("Sicherung erstellen" on the settings page):
    one ZIP file with a verified copy of the business database and the
    stored signed join declarations, written to a folder the operator can
    reach (add-on: /share/shareomat/backups, visible over Samba).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    * The database is copied with SQLite's online backup API (consistent,
      includes what is still in the WAL, only reads the source — a running
      billing release, which checks PRAGMA data_version, is not disturbed).
    * The copy must pass PRAGMA integrity_check and show a schema version,
      and the finished ZIP is re-read and checked against its manifest
      before it gets its final name. Anything that fails leaves no file
      behind (fail closed).
    * Never contains add-on options or credentials: only shareomat.db,
      the declaration documents and a manifest with SHA-256 per file.
    * File names are generated here and validated on every read, so a
      download request can never name a file outside the backup folder.
    * Nothing is ever deleted here; old backups are removed by the
      operator.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

BACKUP_FORMAT = 1
_NAME = re.compile(r"^shareomat-sicherung-(\d{8}T\d{6}Z)\.zip$")
_DOCUMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DB_ENTRY = "shareomat.db"
_DOCUMENTS_ENTRY = "documents/beitritte/"
_MANIFEST_ENTRY = "manifest.json"
_LOCK = threading.Lock()   # one backup at a time: same-second names can never overwrite each other


class BackupError(Exception):
    """A backup could not be created or verified; nothing was written."""


@dataclass
class BackupInfo:
    name: str
    path: Path
    size: int
    created_at: datetime


def backup_dir(runtime) -> Path:
    """The configured backup folder; without one, <state>/backups."""
    configured = getattr(runtime.paths, "backups", None)
    return Path(configured) if configured else Path(runtime.paths.state) / "backups"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_database(db_path: Path, target: Path) -> int:
    """Consistent copy via the backup API; returns the schema version, raises if unusable."""
    try:
        source = sqlite3.connect(db_path, timeout=30)   # only read: the backup API never writes the source
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
            source.close()
        check = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
        try:
            integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
            has_versions = check.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
            ).fetchone()
            version = check.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] if has_versions else None
        finally:
            check.close()
    except sqlite3.Error as exc:
        raise BackupError(f"Die Datenbank konnte nicht kopiert werden ({exc}).") from exc
    if integrity != "ok":
        raise BackupError(f"Die Kopie der Datenbank ist nicht in Ordnung ({integrity}).")
    if not version:
        raise BackupError("Die Kopie der Datenbank enthält keine Schema-Version.")
    return int(version)


def _documents(documents_dir: Path | None) -> list[Path]:
    """Regular files with a plain name directly in the documents folder (no links, no subfolders)."""
    if documents_dir is None or not documents_dir.is_dir():
        return []
    found = []
    for entry in sorted(documents_dir.iterdir()):
        if entry.is_symlink() or not entry.is_file():
            continue
        if not _DOCUMENT_NAME.match(entry.name):
            continue
        found.append(entry)
    return found


def _verify_zip(path: Path, manifest: dict) -> None:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise BackupError("Die Sicherungsdatei konnte nicht fehlerfrei gelesen werden.")
        stored = json.loads(archive.read(_MANIFEST_ENTRY))
        if stored != manifest:
            raise BackupError("Das Inhaltsverzeichnis der Sicherung stimmt nicht.")
        for item in manifest["files"]:
            if hashlib.sha256(archive.read(item["path"])).hexdigest() != item["sha256"]:
                raise BackupError(f"Prüfsumme von {item['path']} stimmt nicht.")


def create_backup(
    db_path: Path, target_dir: Path, *, documents_dir: Path | None = None, now: datetime | None = None,
) -> BackupInfo:
    """Write one verified backup ZIP into target_dir and return it. Raises BackupError; leaves nothing half-written."""
    db_path = Path(db_path)
    if not db_path.is_file():
        raise BackupError("Die Datenbank wurde nicht gefunden.")
    created = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    name = f"shareomat-sicherung-{created.strftime('%Y%m%dT%H%M%SZ')}.zip"
    target_dir = Path(target_dir)
    with _LOCK:
        return _create_locked(db_path, target_dir, name, created, documents_dir)


def _create_locked(db_path: Path, target_dir: Path, name: str, created: datetime, documents_dir) -> BackupInfo:
    target_dir.mkdir(parents=True, exist_ok=True)
    final = target_dir / name
    if final.exists():
        raise BackupError("Eine Sicherung mit diesem Zeitstempel besteht bereits. Bitte kurz warten.")

    with tempfile.TemporaryDirectory(prefix=".sicherung-", dir=target_dir) as work:
        work_dir = Path(work)
        db_copy = work_dir / _DB_ENTRY
        schema_version = _copy_database(db_path, db_copy)
        files = [{"path": _DB_ENTRY, "size": db_copy.stat().st_size, "sha256": _sha256(db_copy)}]
        documents = _documents(documents_dir)
        for document in documents:
            files.append({"path": _DOCUMENTS_ENTRY + document.name, "size": document.stat().st_size,
                          "sha256": _sha256(document)})
        manifest = {
            "format": BACKUP_FORMAT,
            "created_at": created.isoformat(),
            "schema_version": schema_version,
            "files": files,
        }
        part = work_dir / (name + ".part")
        with zipfile.ZipFile(part, "x", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_copy, _DB_ENTRY)
            for document in documents:
                archive.write(document, _DOCUMENTS_ENTRY + document.name)
            archive.writestr(_MANIFEST_ENTRY, json.dumps(manifest, ensure_ascii=False, indent=2))
        _verify_zip(part, manifest)
        if final.exists():
            raise BackupError("Eine Sicherung mit diesem Zeitstempel besteht bereits. Bitte kurz warten.")
        os.replace(part, final)
    return BackupInfo(name=name, path=final, size=final.stat().st_size, created_at=created)


def _info(path: Path) -> BackupInfo | None:
    match = _NAME.match(path.name)
    if match is None or path.is_symlink() or not path.is_file():
        return None
    created = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return BackupInfo(name=path.name, path=path, size=path.stat().st_size, created_at=created)


def list_backups(target_dir: Path) -> list[BackupInfo]:
    """Backups in target_dir, newest first (only files with the generated name)."""
    target_dir = Path(target_dir)
    if not target_dir.is_dir():
        return []
    found = [info for info in (_info(p) for p in target_dir.iterdir()) if info is not None]
    return sorted(found, key=lambda info: info.created_at, reverse=True)


def find_backup(target_dir: Path, name: str) -> BackupInfo | None:
    """The backup called `name` in target_dir, or None. Only generated names are accepted."""
    if not isinstance(name, str) or _NAME.match(name) is None:
        return None
    return _info(Path(target_dir) / name)
