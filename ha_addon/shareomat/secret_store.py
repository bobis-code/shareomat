# -*- coding: utf-8 -*-
"""
File: shareomat/secret_store.py

Purpose:
    Credentials outside the database (ADR-0012 point 10, P3): the secret
    store entered through the web UI, and the resolution of a secret over
    all sources in a fixed order.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Sources of a secret, the first one that has it wins:
        1. environment variable SHAREOMAT_… (e.g. SHAREOMAT_ENTSOE_API_TOKEN)
        2. the secrets file next to the configuration (shareomat.config:
           written by the HA add-on from its options, by hand otherwise)
        3. this store (`secrets.json` next to the database, set in the web UI)
    Sources 1 and 2 reach this module through register_fixed_secrets() at
    startup; a secret from there is shown as "not changeable here".

    The store is a JSON file only Shareomat may read (0600). It is never part
    of the Shareomat backup (shareomat.database.backup copies the database
    and the documents only), never logged and never shown: the web UI only
    says "hinterlegt" / "nicht hinterlegt". On Linux a store that other users
    can read is refused instead of used.
"""

from __future__ import annotations

import json
import logging
import os
import stat
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

STORE_FILENAME = "secrets.json"
ENTSOE_API_TOKEN = "entsoe.api_token"
KNOWN_SECRETS = (ENTSOE_API_TOKEN,)

SOURCE_STORE = "store"        # set in the web UI; the other sources: shareomat.config.SECRET_SOURCE_*

_lock = threading.Lock()
_fixed: dict[str, tuple[str, str]] = {}     # key -> (value, source) from environment or secrets file


class SecretStoreError(Exception):
    """The store can't be read or written safely (wrong permissions, broken file)."""


# ── Private helpers ──────────────────────────────────────────────────────────


def check_private_file(path: Path) -> None:
    """On Linux the file must not be readable by group or others (Windows has no such mode bits)."""
    if os.name != "posix":
        return
    mode = path.stat().st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SecretStoreError(
            f"{path} ist für andere Benutzer lesbar (Rechte {stat.filemode(mode)}). "
            f"Nur Shareomat darf sie lesen: chmod 600 {path}"
        )


def _read(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    check_private_file(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SecretStoreError(f"{path} kann nicht gelesen werden: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in data.items()):
        raise SecretStoreError(f"{path} hat kein gültiges Format.")
    return data


def _write(path: Path, data: dict[str, str]) -> None:
    """Atomically, and private from the first byte (created 0600, then renamed over the old file)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".secrets-", suffix=".tmp", dir=path.parent)
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


# ── Public interface ─────────────────────────────────────────────────────────


def store_path(db_path: Path) -> Path:
    """`secrets.json` next to the database, or SHAREOMAT_SECRET_STORE_PATH."""
    override = os.environ.get("SHAREOMAT_SECRET_STORE_PATH", "").strip()
    return Path(override) if override else Path(db_path).parent / STORE_FILENAME


class SecretStore:
    """Secrets set in the web UI. Values never appear in repr, logs or errors."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def __repr__(self) -> str:
        return f"SecretStore(path={self.path})"

    def get(self, key: str) -> str:
        with _lock:
            return _read(self.path).get(key, "")

    def has(self, key: str) -> bool:
        return bool(self.get(key))

    def set(self, key: str, value: str) -> None:
        if key not in KNOWN_SECRETS:
            raise SecretStoreError(f"Unbekannter Schlüssel: {key}")
        value = value.strip()
        if not value:
            raise SecretStoreError("Leerer Wert: zum Entfernen «Löschen» verwenden.")
        with _lock:
            data = _read(self.path)
            data[key] = value
            _write(self.path, data)
        logger.info("Secret %s stored in %s", key, self.path)

    def delete(self, key: str) -> bool:
        with _lock:
            data = _read(self.path)
            if key not in data:
                return False
            del data[key]
            _write(self.path, data)
        logger.info("Secret %s removed from %s", key, self.path)
        return True


def register_fixed_secrets(secrets: dict[str, tuple[str, str]]) -> None:
    """Called once at startup: secrets from the environment or the secrets file, as (value, source)."""
    with _lock:
        _fixed.clear()
        _fixed.update({k: v for k, v in secrets.items() if v[0]})


def resolve(db_path: Path, key: str) -> tuple[str, str]:
    """(value, source) of a secret; ("", "") if it is set nowhere. A broken store counts as not set."""
    with _lock:
        fixed = _fixed.get(key)
    if fixed is not None:
        return fixed
    try:
        value = SecretStore(store_path(db_path)).get(key)
    except SecretStoreError as exc:
        logger.error("Secret store not usable: %s", exc)
        return "", ""
    return (value, SOURCE_STORE) if value else ("", "")


def entsoe_api_token(db_path: Path) -> str:
    """The ENTSO-E token, from wherever it is set (see the module notes)."""
    return resolve(db_path, ENTSOE_API_TOKEN)[0]


def move_database_secrets(db_path: Path) -> list[str]:
    """Move secrets an older version kept in the `settings` table into the store, then delete them there.

    Until 0.6.34 the ENTSO-E token was a row of `settings` and so part of every
    backup. A value already in the store wins; the database row goes either way.
    If the store can't be written, the row stays (nothing is lost) and the error
    is logged. Returns the keys that were moved or dropped.

    A deleted SQLite row stays in the file until its page is reused, and a
    backup copies every page. So the row is deleted with secure_delete, and
    after a move the database is rebuilt once (VACUUM), which also drops old
    versions of the value from free pages; the WAL is truncated after it.
    """
    from shareomat.database.audit import log_event
    from shareomat.database.sqlite import connect

    legacy = {"entsoe_api_token": ENTSOE_API_TOKEN}
    with connect(db_path) as conn:
        rows = conn.execute(
            f"SELECT setting_key, setting_value FROM settings WHERE setting_key IN ({','.join('?' * len(legacy))})",
            tuple(legacy),
        ).fetchall()
    moved = []
    store = SecretStore(store_path(db_path))
    for row in rows:
        key = legacy[row["setting_key"]]
        value = (row["setting_value"] or "").strip()
        try:
            if value and not store.has(key):
                store.set(key, value)
        except (SecretStoreError, OSError) as exc:
            logger.error("Secret %s stays in the database, the secret store can't be written: %s", key, exc)
            continue
        with connect(db_path) as conn:
            conn.execute("PRAGMA secure_delete = ON")
            with conn:
                conn.execute("DELETE FROM settings WHERE setting_key = ?", (row["setting_key"],))
        log_event(db_path, "secrets", key, "moved",
                  "aus der Datenbank in den Geheimnisspeicher" if value else "leerer Eintrag aus der Datenbank entfernt")
        moved.append(key)
    if moved:
        _rebuild_without_remnants(db_path)
    return moved


def _rebuild_without_remnants(db_path: Path) -> None:
    """VACUUM (needs autocommit) and truncate the WAL, so no copy of a removed secret stays on disk."""
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        conn.close()
