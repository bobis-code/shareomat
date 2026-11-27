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
    * Every new backup passes a restore probe before it gets its final name:
      the ZIP is checked entry by entry and the database is extracted and
      opened like a restore would do (P0b of the rebuild, 0.6.32).
    * restore_backup() puts a backup back into a data folder (command line:
      python -m shareomat.restore). All checks run on an extracted copy first;
      existing data is moved aside, never deleted or overwritten.
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

from shareomat.database.sqlite import _SCHEMA_VERSION, now_iso

BACKUP_FORMAT = 1
_NAME = re.compile(r"^shareomat-sicherung-(\d{8}T\d{6}Z)\.zip$")
_DOCUMENT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DB_ENTRY = "shareomat.db"
_DOCUMENTS_ENTRY = "documents/beitritte/"
_MANIFEST_ENTRY = "manifest.json"
_LOCK = threading.Lock()   # one backup at a time: same-second names can never overwrite each other


class BackupError(Exception):
    """A backup could not be created or verified; nothing was written."""


class RestoreError(Exception):
    """A backup could not be restored; the existing data is unchanged."""


@dataclass
class RestoreResult:
    db_path: Path
    schema_version: int
    backup_created_at: str
    documents: int
    sessions_revoked: int
    moved_aside: Path | None      # where the data that was there before now is (None: the folder was empty)


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


def _allowed_entry(path) -> bool:
    """Only the database and plain document names in the documents folder (no path tricks)."""
    if not isinstance(path, str):
        return False
    if path == _DB_ENTRY:
        return True
    return path.startswith(_DOCUMENTS_ENTRY) and bool(_DOCUMENT_NAME.match(path[len(_DOCUMENTS_ENTRY):]))


def _stream_sha256(archive: zipfile.ZipFile, name: str) -> str:
    digest = hashlib.sha256()
    with archive.open(name) as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checked_manifest(archive: zipfile.ZipFile) -> dict:
    """The manifest of a backup ZIP after checking every entry: names, sizes and SHA-256. Raises BackupError."""
    names = archive.namelist()
    if _MANIFEST_ENTRY not in names:
        raise BackupError("Die Sicherung enthält kein Inhaltsverzeichnis (manifest.json).")
    try:
        manifest = json.loads(archive.read(_MANIFEST_ENTRY))
    except (ValueError, UnicodeDecodeError) as exc:
        raise BackupError("Das Inhaltsverzeichnis der Sicherung ist nicht lesbar.") from exc
    if not isinstance(manifest, dict) or manifest.get("format") != BACKUP_FORMAT:
        raise BackupError("Unbekanntes Format der Sicherung.")
    files = manifest.get("files")
    if not isinstance(files, list) or not all(isinstance(item, dict) for item in files):
        raise BackupError("Das Inhaltsverzeichnis der Sicherung ist unvollständig.")
    listed = [item.get("path") for item in files]
    for path in listed:
        if not _allowed_entry(path):
            raise BackupError(f"Unerwarteter Eintrag in der Sicherung: {path!r}.")
    if len(set(listed)) != len(listed) or _DB_ENTRY not in listed:
        raise BackupError("Das Inhaltsverzeichnis der Sicherung ist unvollständig.")
    if sorted(names) != sorted(listed + [_MANIFEST_ENTRY]):
        raise BackupError("Die Sicherung enthält Dateien, die nicht im Inhaltsverzeichnis stehen, oder es fehlen welche.")
    for item in files:
        if archive.getinfo(item["path"]).file_size != item.get("size"):
            raise BackupError(f"Grösse von {item['path']} stimmt nicht.")
        if _stream_sha256(archive, item["path"]) != item.get("sha256"):
            raise BackupError(f"Prüfsumme von {item['path']} stimmt nicht.")
    return manifest


def _extract(archive: zipfile.ZipFile, name: str, target: Path) -> None:
    """Stream one checked entry to `target` (never through ZipFile.extract, which trusts the entry path)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with archive.open(name) as source, target.open("xb") as sink:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sink.write(chunk)


def _inspect_database(path: Path) -> tuple[str, int | None]:
    """(integrity_check result, highest schema version) of a database file, opened read-only."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        has_versions = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
        ).fetchone()
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] if has_versions else None
    finally:
        conn.close()
    return integrity, int(version) if version is not None else None


def _probe_restore(zip_path: Path, work_dir: Path) -> None:
    """Open a finished backup like a restore would: check entries, extract the database, check it."""
    probe = work_dir / "probe"
    probe.mkdir()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            manifest = _checked_manifest(archive)
            _extract(archive, _DB_ENTRY, probe / _DB_ENTRY)
        integrity, version = _inspect_database(probe / _DB_ENTRY)
    except (zipfile.BadZipFile, OSError, sqlite3.Error) as exc:
        raise BackupError(f"Die Wiederherstellungsprobe ist fehlgeschlagen ({exc}).") from exc
    if integrity != "ok" or version != manifest.get("schema_version"):
        raise BackupError(f"Die Wiederherstellungsprobe ist fehlgeschlagen ({integrity}, Schema {version}).")


def _refuse_if_in_use(db_path: Path) -> None:
    """Best effort: a database another process is writing to cannot be replaced. Shareomat must be stopped."""
    if not db_path.exists():
        return
    try:
        conn = sqlite3.connect(db_path, timeout=0)
        try:
            conn.execute("BEGIN EXCLUSIVE")
            conn.rollback()
        finally:
            conn.close()
    except sqlite3.OperationalError as exc:
        raise RestoreError("Die Datenbank ist in Gebrauch. Shareomat zuerst beenden, dann erneut versuchen.") from exc


def _after_restore(db_path: Path, backup_name: str, manifest: dict) -> int:
    """On the restored copy: end every login session and record the restore in the audit log."""
    conn = sqlite3.connect(db_path)
    try:
        with conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            revoked = 0
            if "auth_sessions" in tables:
                revoked = conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE revoked_at IS NULL",
                                       (now_iso(),)).rowcount
            if "audit_events" in tables:
                columns = {r[1] for r in conn.execute("PRAGMA table_info(audit_events)")}
                detail = (f"Sicherung vom {manifest.get('created_at', '?')} wiederhergestellt "
                          f"(Schema {manifest.get('schema_version')}); {revoked} Sitzung(en) beendet")
                values = {"occurred_at": now_iso(), "entity": "backup", "entity_id": backup_name,
                          "action": "restored", "detail": detail}
                if "actor" in columns:
                    values["actor"] = "system"
                conn.execute(f"INSERT INTO audit_events ({', '.join(values)}) VALUES ({', '.join('?' * len(values))})",
                             tuple(values.values()))
    finally:
        conn.close()
    return revoked


def _move_aside(db_path: Path, stamp: str) -> Path | None:
    """Move the database (with WAL files), the documents and the recomputable side databases to a new folder."""
    folder = db_path.parent
    candidates = [db_path, db_path.with_name(db_path.name + "-wal"), db_path.with_name(db_path.name + "-shm"),
                  folder / "documents"]
    for side in ("live.db", "coordinator.db"):
        candidates += [folder / side, folder / (side + "-wal"), folder / (side + "-shm")]
    present = [c for c in candidates if c.exists()]
    if not present:
        return None
    aside = folder / f"vor-wiederherstellung-{stamp}"
    aside.mkdir()
    for item in present:
        os.replace(item, aside / item.name)
    return aside


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
        _probe_restore(part, work_dir)
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


def restore_backup(zip_path: Path, db_path: Path, *, now: datetime | None = None) -> RestoreResult:
    """Put a backup ZIP back as `db_path` (and its documents next to it). Raises RestoreError.

    Shareomat must not run meanwhile. Every check runs on an extracted copy first: entries, sizes and
    SHA-256 against the manifest, integrity_check, schema version (a newer schema than this program
    knows is refused; an older one is migrated by the next start, with its own verified copy). Only
    then is whatever is in the folder moved aside to vor-wiederherstellung-<time>/ and the copy moved
    in; if that last step fails, the old data is moved back. On the restored database every login
    session is ended and the restore is recorded in the audit log.
    """
    zip_path, db_path = Path(zip_path), Path(db_path)
    if not zip_path.is_file():
        raise RestoreError("Die Sicherungsdatei wurde nicht gefunden.")
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _refuse_if_in_use(db_path)
    with tempfile.TemporaryDirectory(prefix=".wiederherstellung-", dir=db_path.parent) as work:
        work_dir = Path(work)
        try:
            with zipfile.ZipFile(zip_path) as archive:
                manifest = _checked_manifest(archive)
                for item in manifest["files"]:
                    _extract(archive, item["path"], work_dir / item["path"])
        except BackupError as exc:
            raise RestoreError(str(exc)) from exc
        except (zipfile.BadZipFile, OSError) as exc:
            raise RestoreError(f"Die Sicherungsdatei ist nicht lesbar ({exc}).") from exc
        for item in manifest["files"]:
            if _sha256(work_dir / item["path"]) != item["sha256"]:
                raise RestoreError(f"Prüfsumme von {item['path']} stimmt nach dem Entpacken nicht.")
        restored = work_dir / _DB_ENTRY
        try:
            integrity, version = _inspect_database(restored)
        except sqlite3.Error as exc:
            raise RestoreError(f"Die gesicherte Datenbank lässt sich nicht öffnen ({exc}).") from exc
        if integrity != "ok":
            raise RestoreError(f"Die gesicherte Datenbank ist nicht in Ordnung ({integrity}).")
        if version is None or version != manifest.get("schema_version"):
            raise RestoreError("Die Schema-Version der gesicherten Datenbank passt nicht zum Inhaltsverzeichnis.")
        if version > _SCHEMA_VERSION:
            raise RestoreError(f"Die Sicherung stammt von einer neueren Shareomat-Version (Schema {version}); "
                               f"diese Version kennt Schema {_SCHEMA_VERSION}. Zuerst Shareomat aktualisieren.")
        revoked = _after_restore(restored, zip_path.name, manifest)
        documents = sum(1 for item in manifest["files"] if item["path"] != _DB_ENTRY)

        moved_aside = _move_aside(db_path, stamp)
        try:
            os.replace(restored, db_path)
            if (work_dir / "documents").exists():
                os.replace(work_dir / "documents", db_path.parent / "documents")
        except OSError as exc:
            if moved_aside is not None:
                for item in list(moved_aside.iterdir()):
                    target = db_path.parent / item.name
                    if not target.exists():
                        os.replace(item, target)
            raise RestoreError(f"Die wiederhergestellten Dateien konnten nicht an ihren Platz ({exc}); "
                               "der bisherige Stand ist wieder da.") from exc
    return RestoreResult(db_path=db_path, schema_version=version, backup_created_at=str(manifest.get("created_at", "")),
                         documents=documents, sessions_revoked=revoked, moved_aside=moved_aside)
