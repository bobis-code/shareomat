# -*- coding: utf-8 -*-
"""
File: shareomat/core/collector/swisseldex_receiver.py

Purpose:
    Receives files from the Swisseldex Datahub mailbox and keeps them in a
    local receive archive: connection test, manual and automatic sync, the
    60-second login gate, duplicate detection, a bounded run log and the
    warning when syncing stops for too long. Stage SX-1 of
    docs/development/Swisseldex-SDAT-Auftrag_2026-11-12.md.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Receiving only. Files are archived exactly as downloaded and are not
    put into the engine inbox or billed here. After a successful sync the
    optional `importer` (SX-2, shareomat.core.collector.sdat_import, wired in
    main.py) checks and imports the newly archived files; its result is part
    of the run record. Nothing on the Datahub is ever deleted, renamed or
    uploaded, and no acknowledgements are sent (open question, see the brief).

    Like the e-mail importer, the receiver keeps its state in a JSON file in
    the state directory (`<state>/swisseldex/state.json`), not in
    shareomat.db: a sync never writes to the business database, so it can
    neither change master data nor disturb the release of a billing run
    (which refuses when PRAGMA data_version moves). A corrupt state file is
    never reset silently; the receiver then refuses to run until someone
    looked at it.

    Order of a received file, so that a crash at any point loses nothing and
    counts nothing twice: download into an exclusive `.part` file, check the
    byte count against the listed size, fsync, move to its final name
    (`<sha256 prefix>_<remote name>`), then record it in the state file.
    Leftover `.part` files are removed at start; an unrecorded file is
    simply downloaded again by the next sync.

    Raw backup (ADR-0010): downloads are only staged during the session.
    After it is closed (the Datahub drops idle sessions after 10 s), every
    staged file is first copied write-once to the network storage and
    verified (shareomat.core.collector.raw_backup), and only then archived
    (the .gz check reads it) and offered to the import. With the mode
    "required" a file without a verified backup is neither archived nor
    recorded as seen, so the next sync downloads it again; received files
    without a verified backup are never handed to the import.

    The Datahub refuses a second login within 60 s (manual 4.4). Every
    session - test, manual or automatic sync - passes one gate; the last
    login time is persisted, so a restart does not bypass it either. A
    request that has to wait is queued and runs in the background.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import re
import secrets
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from shareomat.config import SwisseldexConfig
from shareomat.core.collector.raw_backup import RawBackupError
from shareomat.core.collector.swisseldex_ftpes import (
    MAX_FILE_BYTES,
    DownloadAborted,
    RemoteFile,
    SwisseldexFtpError,
    SwisseldexFtpesSession,
)

logger = logging.getLogger(__name__)

KIND_TEST = "test"
KIND_SYNC = "sync"
TRIGGER_MANUAL = "manual"
TRIGGER_AUTO = "auto"

FILE_RECEIVED = "received"
FILE_NEEDS_REVIEW = "needs_review"

BACKUP_OK = "ok"
BACKUP_BYPASS = "bypass"
BACKUP_ERROR = "error"

RESULT_OK = "ok"
RESULT_WARNING = "warning"
RESULT_ERROR = "error"

# Manual 4.4: repeated logins within 60 s are refused. One second of margin.
MIN_LOGIN_INTERVAL_SECONDS = 61.0
# The Datahub deletes messages that were not fetched after 30 days; warn well before.
OVERDUE_AFTER_DAYS = 7
# Automatic sync only: retries after a transient failure, then wait for the next interval.
AUTO_RETRY_DELAYS_SECONDS = (300.0, 900.0)

_ALLOWED_SUFFIXES = (".xml", ".xml.gz", ".txt", ".txt.gz", ".csv", ".csv.gz")
_MAX_UNPACKED_BYTES = 512 * 1024 * 1024
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_STATE_VERSION = 1
_MAX_STATE_BYTES = 20 * 1024 * 1024
_MAX_RUNS = 200
_MAX_SEEN = 50_000
_IDLE_WAIT_SECONDS = 30.0
_MAX_BACKUP_FAILURES = 500

_BACKUP_WARNING = (
    "Rohdatensicherung: {count} Swisseldex-Datei(en) konnten nicht auf dem Netzwerkspeicher gesichert werden "
    "und werden nicht verarbeitet. Details unter «Externe Daten»."
)

_OVERDUE_WARNING = (
    "Swisseldex: seit {days} Tagen keine erfolgreiche Synchronisation. Nicht abgeholte "
    "Dateien löscht Swisseldex nach 30 Tagen. Bitte unter «Externe Daten» synchronisieren."
)


class SwisseldexStateError(RuntimeError):
    """The state file exists but cannot be trusted; it is left untouched."""


@dataclass(frozen=True)
class RequestOutcome:
    """Answer to a test/sync request from the web UI."""

    accepted: bool
    run_id: int | None
    starts_in_seconds: float
    message: str


# ── Private helpers ───────────────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _from_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _empty_state() -> dict[str, Any]:
    return {
        "version": _STATE_VERSION,
        "next_run_id": 1,
        "last_login_epoch": None,
        "last_connection_at": None,
        "last_connection_ok": None,
        "last_sync_attempt_at": None,
        "last_sync_success_at": None,
        "remote_count": None,
        "files": {},
        "seen": {},
        "skipped": {},
        "backup_failures": {},
        "runs": [],
    }


def _read_state(path: Path) -> dict[str, Any]:
    """Bounded and validated. Missing file = fresh state; broken file = SwisseldexStateError."""
    if not path.exists():
        return _empty_state()
    if path.stat().st_size > _MAX_STATE_BYTES:
        raise SwisseldexStateError(f"state file larger than {_MAX_STATE_BYTES} bytes: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SwisseldexStateError(f"state file unreadable: {path}: {exc}") from exc
    if not isinstance(state, dict) or state.get("version") != _STATE_VERSION:
        raise SwisseldexStateError(f"state file has an unknown format: {path}")
    merged = _empty_state()
    merged.update(state)
    for field_name in ("files", "seen", "skipped", "backup_failures"):
        if not isinstance(merged[field_name], dict):
            raise SwisseldexStateError(f"state field {field_name} is invalid: {path}")
    if not isinstance(merged["runs"], list) or not isinstance(merged["next_run_id"], int):
        raise SwisseldexStateError(f"state run log is invalid: {path}")
    return merged


def _write_state(path: Path, state: dict[str, Any]) -> None:
    """Atomic replace: a crash leaves either the old or the new file, never half of one."""
    encoded = json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
    temp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    with temp.open("xb") as f:
        f.write(encoded)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)


def _name_problem(name: str) -> str:
    """Why a remote name must not be downloaded, or "" if it is fine.

    Server names never become paths as they are: only a plain, bounded
    character set and the extensions the Datahub itself accepts.
    """
    if not _SAFE_NAME.match(name) or ".." in name:
        return "Dateiname nicht zulässig"
    if not name.lower().endswith(_ALLOWED_SUFFIXES):
        return "Dateiendung nicht unterstützt"
    return ""


def _gzip_problem(path: Path) -> str:
    """A .gz must unpack completely and within 512 MB (Datahub limit), else it needs review."""
    total = 0
    try:
        with gzip.open(path, "rb") as packed:
            while chunk := packed.read(1024 * 1024):
                total += len(chunk)
                if total > _MAX_UNPACKED_BYTES:
                    return "entpackt grösser als 512 MB"
    except (OSError, EOFError, zlib.error) as exc:
        return f"gz-Datei beschädigt ({' '.join(str(exc).split())[:120]})"
    return ""


def _remote_key(remote: RemoteFile) -> str | None:
    """Identity of a listed file without downloading it; None if the server gave too little."""
    if remote.size is None or not remote.modified:
        return None
    return f"{remote.name}|{remote.size}|{remote.modified}"


def _fsync_dir(directory: Path) -> None:
    """Make the rename durable where the OS supports it (not on Windows)."""
    if os.name != "posix":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _run_message(record: dict[str, Any]) -> str:
    if record["result"] == RESULT_ERROR:
        return record["message"]
    if record["kind"] == KIND_TEST:
        return f"Verbindung erfolgreich – {record['listed']} Datei(en) vorhanden."
    if record["listed"] == 0:
        return "Verbindung erfolgreich. Postfach leer – keine neuen Dateien."
    parts = [f"Verbindung erfolgreich. Synchronisation abgeschlossen. {record['downloaded']} neue Datei(en)"]
    if record["known"]:
        parts.append(f"{record['known']} bereits bekannt")
    if record["duplicates"]:
        parts.append(f"{record['duplicates']} Inhalt bereits vorhanden")
    if record["review"]:
        parts.append(f"{record['review']} benötigt Prüfung")
    if record.get("imported"):
        parts.append(f"{record['imported']} importiert")
    if record.get("not_imported"):
        parts.append(f"{record['not_imported']} nicht importiert")
    if record.get("import") == "error":
        parts.append("Import fehlgeschlagen")
    if record.get("backed_up"):
        parts.append(f"{record['backed_up']} auf dem Netzwerkspeicher gesichert")
    if record.get("backfilled"):
        parts.append(f"{record['backfilled']} nachträglich gesichert")
    if record.get("backup_failed"):
        parts.append(f"{record['backup_failed']} NICHT gesichert – nicht verarbeitet")
    if record.get("backup_bypass"):
        parts.append("Rohdatensicherung ausgeschaltet (nur lokal)")
    return ", ".join(parts) + "."


# ── Public interface ──────────────────────────────────────────────────────────


class SwisseldexReceiver:
    """The Swisseldex mailbox receiver: one worker thread, one session at a time.

    `session_factory` and `clock` exist for tests. Production builds
    SwisseldexFtpesSession from the config and uses wall-clock time (the
    login gate survives a restart, so it must be wall clock).
    `warn`/`unwarn` show and remove the overdue warning in the web UI.
    `raw_backup` (shareomat.core.collector.raw_backup.RawBackupGuard) backs
    up every new file before it is archived; None keeps the old behaviour.
    """

    def __init__(
        self,
        config: SwisseldexConfig,
        base_dir: Path,
        *,
        session_factory: Callable[[], SwisseldexFtpesSession] | None = None,
        clock: Callable[[], float] = time.time,
        warn: Callable[[str], None] | None = None,
        unwarn: Callable[[str], None] | None = None,
        importer: Callable[[list[tuple[Path, str, str]], bool], list | None] | None = None,
        raw_backup: Any = None,
    ) -> None:
        self._config = config
        self._importer = importer
        self._raw_backup = raw_backup
        self._shown_backup_warning = ""
        self._base_dir = base_dir
        self._received_dir = base_dir / "received"
        self._state_path = base_dir / "state.json"
        self._session_factory = session_factory or self._default_session
        self._clock = clock
        self._warn = warn or (lambda message: None)
        self._unwarn = unwarn or (lambda message: None)
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._pending: tuple[str, str, int] | None = None
        self._running: tuple[str, str, int] | None = None
        self._auto_retry_index = 0
        self._auto_retry_at: float | None = None
        self._shown_warning = ""
        self._state_error = ""
        self._state: dict[str, Any] = _empty_state()
        self._load()

    # ── lifecycle ──

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="swisseldex-receiver", daemon=True)
        self._thread.start()
        logger.info(
            "Swisseldex receiver started (automatic sync %s)",
            f"every {self._config.sync_interval_hours} h" if self._config.auto_sync else "off",
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Ends a running download as incomplete (never recorded) and waits briefly."""
        self._stop.set()
        with self._changed:
            self._changed.notify_all()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("Swisseldex receiver still busy at shutdown")

    # ── requests from the web UI ──

    def request(self, kind: str, trigger: str = TRIGGER_MANUAL) -> RequestOutcome:
        """Queue a test or sync. Never blocks; the login gate is waited for in the background."""
        if kind not in (KIND_TEST, KIND_SYNC):
            raise ValueError(kind)
        with self._changed:
            refusal = self._refusal()
            if refusal:
                return RequestOutcome(False, None, 0.0, refusal)
            if self._pending is not None:
                return RequestOutcome(False, None, 0.0, "Es ist bereits ein Swisseldex-Vorgang geplant.")
            if self._running is not None and self._running[0] == kind == KIND_SYNC:
                return RequestOutcome(False, None, 0.0, "Eine Synchronisation läuft bereits.")
            run_id = self._allocate_run_id()
            self._pending = (kind, trigger, run_id)
            starts_in = self.login_wait_seconds()
            self._changed.notify_all()
        if self._running is not None:
            message = "Wird nach dem laufenden Vorgang ausgeführt."
        elif starts_in > 0:
            message = (f"Geplant – startet in {int(starts_in + 0.999)} s "
                       "(Swisseldex erlaubt nur eine Anmeldung pro Minute).")
        else:
            message = "Gestartet."
        return RequestOutcome(True, run_id, starts_in, message)

    def wait_for_run(self, run_id: int, timeout: float) -> dict[str, Any] | None:
        """The finished run record, or None if it did not finish within `timeout`."""
        deadline = time.monotonic() + timeout
        with self._changed:
            while True:
                record = self._find_run(run_id)
                if record is not None:
                    return dict(record)
                left = deadline - time.monotonic()
                if left <= 0 or self._stop.is_set():
                    return None
                self._changed.wait(min(left, 1.0))

    def login_wait_seconds(self) -> float:
        with self._lock:
            last = self._state.get("last_login_epoch")
        if last is None:
            return 0.0
        return max(0.0, float(last) + MIN_LOGIN_INTERVAL_SECONDS - self._clock())

    def snapshot(self, *, run_limit: int = 20, file_limit: int = 30) -> dict[str, Any]:
        """Everything the status card shows, as plain data (no credentials)."""
        with self._lock:
            state = self._state
            runs = list(reversed(state["runs"]))[:run_limit]
            files = sorted(
                state["files"].values(),
                key=lambda f: (f.get("received_at") or "", f.get("run_id") or 0),
                reverse=True,
            )
            skipped = [{"name": name, **info} for name, info in state["skipped"].items()]
            needs_review = sum(1 for f in state["files"].values() if f.get("status") == FILE_NEEDS_REVIEW)
            backup_failures = sorted(state["backup_failures"].values(),
                                     key=lambda f: f.get("attempted_at") or "", reverse=True)
            not_backed_up = sum(1 for f in state["files"].values()
                                if (f.get("raw_backup") or {}).get("status") != BACKUP_OK)
            return {
                "configured": bool(self._config.username and self._config.password),
                "state_error": self._state_error,
                "host": self._config.host,
                "auto_sync": self._config.auto_sync,
                "interval_hours": self._config.sync_interval_hours,
                "running": self._running[0] if self._running else None,
                "pending": self._pending[0] if self._pending else None,
                "login_wait_seconds": self.login_wait_seconds(),
                "last_connection_at": state["last_connection_at"],
                "last_connection_ok": state["last_connection_ok"],
                "last_sync_success_at": state["last_sync_success_at"],
                "next_auto_at": _iso(self._next_auto_moment()),
                "remote_count": state["remote_count"],
                "last_run": dict(runs[0]) if runs else None,
                "runs": [dict(r) for r in runs],
                "files": [dict(f) for f in files[:file_limit]],
                "skipped": skipped[:file_limit],
                "received_total": len(state["files"]),
                "needs_review_total": needs_review + len(state["skipped"]),
                "overdue_days": self._overdue_days(),
                "raw_backup": self._raw_backup.describe() if self._raw_backup is not None else None,
                "backup_failures": [dict(f) for f in backup_failures[:file_limit]],
                "not_backed_up_total": not_backed_up,
            }

    def received_files(self) -> list[tuple[Path, str, str]]:
        """(archived path, sha256, remote name) of every received file that is not marked for review.

        With a required raw backup only files whose backup was verified: nothing else reaches the import.
        """
        required = self._backup_required()
        with self._lock:
            entries = [dict(f) for f in self._state["files"].values()
                       if f.get("status") == FILE_RECEIVED
                       and (not required or (f.get("raw_backup") or {}).get("status") == BACKUP_OK)]
        entries.sort(key=lambda f: (f.get("received_at") or "", f.get("run_id") or 0))
        return [(self._received_dir / f["stored_as"], f["sha256"], f["name"]) for f in entries]

    def import_now(self) -> list:
        """Check and import received files that are not imported yet (button on the page)."""
        if self._importer is None:
            return []
        self._backfill_backups({"backfilled": 0, "backup_failed": 0}, [])
        self._update_backup_warning()
        return self._importer(self.received_files(), False) or []

    def run_now(self, kind: str, trigger: str = TRIGGER_MANUAL) -> dict[str, Any]:
        """Run one session synchronously, outside the queue and the login gate (tests only)."""
        with self._changed:
            run_id = self._allocate_run_id()
        return self._execute(kind, trigger, run_id)

    # ── internals ──

    def _default_session(self) -> SwisseldexFtpesSession:
        c = self._config
        return SwisseldexFtpesSession(c.host, c.port, c.username, c.password, timeout=c.timeout_seconds)

    def _refusal(self) -> str:
        if self._state_error:
            return ("Swisseldex-Zustandsdatei ist beschädigt und wurde nicht verändert. "
                    f"Bitte prüfen: {self._state_path}")
        if not (self._config.username and self._config.password):
            return "Benutzername oder Passwort fehlt in den Add-on-Optionen (Abschnitt Swisseldex)."
        if self._stop.is_set():
            return "Shareomat wird gerade beendet."
        return ""

    def _load(self) -> None:
        try:
            self._received_dir.mkdir(parents=True, exist_ok=True)
            for leftover in self._received_dir.glob(".incoming-*.part"):
                leftover.unlink(missing_ok=True)
                logger.info("Swisseldex: removed incomplete download %s", leftover.name)
            self._state = _read_state(self._state_path)
        except (SwisseldexStateError, OSError) as exc:
            self._state_error = str(exc)
            logger.error("Swisseldex receiver disabled: %s", exc)

    def _save(self) -> None:
        with self._lock:
            state = self._state
            state["runs"] = state["runs"][-_MAX_RUNS:]
            if len(state["seen"]) > _MAX_SEEN:
                keep = list(state["seen"].items())[-_MAX_SEEN:]
                state["seen"] = dict(keep)
            _write_state(self._state_path, state)

    def _allocate_run_id(self) -> int:
        with self._lock:
            run_id = int(self._state["next_run_id"])
            self._state["next_run_id"] = run_id + 1
            return run_id

    def _find_run(self, run_id: int) -> dict[str, Any] | None:
        for record in reversed(self._state["runs"]):
            if record.get("id") == run_id:
                return record
        return None

    def _next_auto_moment(self) -> datetime | None:
        if not self._config.auto_sync or self._state_error:
            return None
        if self._auto_retry_at is not None:
            return datetime.fromtimestamp(self._auto_retry_at, timezone.utc).replace(microsecond=0)
        last = _from_iso(self._state.get("last_sync_attempt_at"))
        if last is None:
            return _utc_now()
        return last + timedelta(hours=self._config.sync_interval_hours)

    def _overdue_days(self) -> int | None:
        last = _from_iso(self._state.get("last_sync_success_at"))
        if last is None:
            return None
        days = (datetime.fromtimestamp(self._clock(), timezone.utc) - last).days
        return days if days >= OVERDUE_AFTER_DAYS else None

    def _backup_required(self) -> bool:
        return self._raw_backup is not None and self._raw_backup.required()

    def _update_backup_warning(self) -> None:
        """Visible on every page as long as a received file is not backed up while the backup is required."""
        with self._lock:
            count = len(self._state["backup_failures"])
            if self._backup_required():
                count += sum(1 for f in self._state["files"].values()
                             if (f.get("raw_backup") or {}).get("status") == BACKUP_ERROR)
        message = _BACKUP_WARNING.format(count=count) if count else ""
        if message == self._shown_backup_warning:
            return
        if self._shown_backup_warning:
            self._unwarn(self._shown_backup_warning)
        if message:
            self._warn(message)
            logger.warning("Swisseldex: %d file(s) without a verified raw backup", count)
        self._shown_backup_warning = message

    def _update_warning(self) -> None:
        days = self._overdue_days()
        message = _OVERDUE_WARNING.format(days=days) if days is not None else ""
        if message == self._shown_warning:
            return
        if self._shown_warning:
            self._unwarn(self._shown_warning)
        if message:
            self._warn(message)
            logger.warning("Swisseldex: no successful sync for %d days", days)
        self._shown_warning = message

    def _take_job(self) -> tuple[str, str, int] | None:
        """The queued manual request, else an automatic sync that is due, else None."""
        with self._changed:
            if self._pending is not None:
                job, self._pending = self._pending, None
                self._running = job
                return job
            if self._refusal():
                return None
            due = self._next_auto_moment()
            if due is not None and due.timestamp() <= self._clock():
                job = (KIND_SYNC, TRIGGER_AUTO, self._allocate_run_id())
                self._running = job
                return job
            return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._update_warning()
                job = self._take_job()
                if job is None:
                    with self._changed:
                        if self._pending is None and not self._stop.is_set():
                            self._changed.wait(_IDLE_WAIT_SECONDS)
                    continue
                kind, trigger, run_id = job
                while not self._stop.is_set() and (wait := self.login_wait_seconds()) > 0:
                    self._stop.wait(min(wait, 1.0))
                if self._stop.is_set():
                    break
                record = self._execute(kind, trigger, run_id)
                if trigger == TRIGGER_AUTO:
                    self._plan_auto_retry(record)
            except Exception:
                logger.exception("Swisseldex receiver loop failed")
                self._stop.wait(_IDLE_WAIT_SECONDS)
            finally:
                with self._changed:
                    self._running = None
                    self._changed.notify_all()

    def _plan_auto_retry(self, record: dict[str, Any]) -> None:
        """Bounded retries for transient failures of an automatic sync; never for a refused login."""
        if record["result"] != RESULT_ERROR or not record.get("retryable", False):
            self._auto_retry_index = 0
            self._auto_retry_at = None
            return
        if self._auto_retry_index < len(AUTO_RETRY_DELAYS_SECONDS):
            delay = AUTO_RETRY_DELAYS_SECONDS[self._auto_retry_index]
            self._auto_retry_index += 1
            self._auto_retry_at = self._clock() + delay
            logger.info("Swisseldex: automatic retry %d in %d s", self._auto_retry_index, int(delay))
        else:
            self._auto_retry_index = 0
            self._auto_retry_at = None

    def _execute(self, kind: str, trigger: str, run_id: int) -> dict[str, Any]:
        record: dict[str, Any] = {
            "id": run_id, "kind": kind, "trigger": trigger,
            "attempt": self._auto_retry_index + 1 if trigger == TRIGGER_AUTO else 1,
            "started_at": _iso(_utc_now()), "finished_at": None,
            "result": RESULT_ERROR, "connection": "error", "download": "-" if kind == KIND_TEST else "error",
            "listed": 0, "downloaded": 0, "known": 0, "duplicates": 0, "review": 0, "failed": 0,
            "import": "-", "imported": 0, "not_imported": 0,
            "backed_up": 0, "backfilled": 0, "backup_failed": 0, "backup_bypass": 0,
            "message": "", "detail": "", "retryable": False,
        }
        notes: list[str] = []
        staged: list[tuple[Path, RemoteFile, str | None, Any]] = []
        with self._lock:
            self._state["last_login_epoch"] = self._clock()
            if kind == KIND_SYNC:
                self._state["last_sync_attempt_at"] = record["started_at"]
            self._save()
        try:
            session = self._session_factory()
            with session:
                record["connection"] = "ok"
                remote_files = session.list_files()
                record["listed"] = len(remote_files)
                with self._lock:
                    self._state["remote_count"] = len(remote_files)
                if kind == KIND_SYNC:
                    for remote in remote_files:
                        if self._stop.is_set():
                            raise DownloadAborted()
                        item = self._receive(session, remote, record, notes)
                        if item is not None:
                            staged.append(item)
                    record["download"] = "ok"
            if session.close_note:
                notes.append(session.close_note)
            record["result"] = RESULT_OK
        except SwisseldexFtpError as exc:
            record["message"] = exc.user_message
            record["retryable"] = exc.retryable and not isinstance(exc, DownloadAborted)
            notes.append(f"{exc.phase}: {exc.detail}")
            if exc.phase not in ("connect", "tls", "login"):
                record["connection"] = "ok"
        except Exception as exc:  # unexpected: keep the receiver alive, keep the evidence
            logger.exception("Swisseldex %s failed unexpectedly", kind)
            record["message"] = "Unerwarteter Fehler – Details im Add-on-Protokoll"
            record["retryable"] = True
            notes.append(type(exc).__name__)
        # The session is closed: back up first, then archive (files downloaded before an error count too).
        self._complete_staged(staged, record, notes)
        if kind == KIND_SYNC:
            self._backfill_backups(record, notes)
        if record["result"] != RESULT_ERROR and (record["review"] or record["backup_failed"]):
            record["result"] = RESULT_WARNING
        if kind == KIND_SYNC and record["result"] != RESULT_ERROR and self._importer is not None:
            self._run_import(record, notes)
        record["finished_at"] = _iso(_utc_now())
        record["message"] = _run_message(record)
        record["detail"] = " | ".join(notes)[:1000]
        with self._changed:
            self._state["last_connection_at"] = record["started_at"]
            self._state["last_connection_ok"] = record["connection"] == "ok"
            if kind == KIND_SYNC and record["result"] != RESULT_ERROR:
                self._state["last_sync_success_at"] = record["finished_at"]
            self._state["runs"].append(record)
            self._save()
            self._changed.notify_all()
        self._update_warning()
        self._update_backup_warning()
        logger.info(
            "Swisseldex %s (%s) #%d: %s; listed=%d new=%d known=%d duplicates=%d review=%d",
            kind, trigger, run_id, record["result"], record["listed"], record["downloaded"],
            record["known"], record["duplicates"], record["review"],
        )
        return record

    def _run_import(self, record: dict[str, Any], notes: list[str]) -> None:
        """Import after the session is closed - never while the 10-second FTP idle timer runs."""
        try:
            outcomes = self._importer(self.received_files(), True)
        except Exception as exc:  # the import must never take the receiver down
            logger.exception("Swisseldex: import after sync failed")
            record["import"] = "error"
            notes.append(f"Import: {type(exc).__name__}")
            return
        if outcomes is None:   # automatic import not wanted (data source is not SDAT)
            return
        record["imported"] = sum(1 for o in outcomes if o.imported and not o.skipped)
        record["not_imported"] = sum(1 for o in outcomes if not o.imported)
        record["import"] = "warning" if record["not_imported"] else "ok"
        for outcome in outcomes:
            if not outcome.imported:
                notes.append(f"{outcome.file_name}: {outcome.reason_code}")

    def _receive(self, session: SwisseldexFtpesSession, remote: RemoteFile, record: dict,
                 notes: list[str]) -> tuple[Path, RemoteFile, str | None, Any] | None:
        """Download one listed file into a local staging file; None if known, not acceptable or failed.

        Nothing is archived here: _complete_staged() backs it up and archives it after the session.
        """
        key = _remote_key(remote)
        with self._lock:
            if key is not None and key in self._state["seen"]:
                record["known"] += 1
                return
            if remote.name in self._state["skipped"]:
                return
        problem = _name_problem(remote.name)
        if not problem and remote.size is not None and remote.size > MAX_FILE_BYTES:
            problem = "grösser als 50 MB"
        if problem:
            self._skip(remote, problem, record)
            return

        temp = self._received_dir / f".incoming-{secrets.token_hex(8)}.part"
        staged = False
        try:
            with temp.open("xb") as target:
                try:
                    result = session.download(remote.name, target, expected_size=remote.size, should_stop=self._stop)
                except SwisseldexFtpError as exc:
                    if exc.retryable or isinstance(exc, DownloadAborted):
                        raise
                    record["failed"] += 1
                    target.close()
                    self._skip(remote, exc.user_message, record)
                    return None
                target.flush()
                os.fsync(target.fileno())
            if result.note:
                notes.append(f"{remote.name}: {result.note}")
            staged = True
            return temp, remote, key, result
        finally:
            if not staged:
                temp.unlink(missing_ok=True)

    def _complete_staged(self, staged: list, record: dict, notes: list[str]) -> None:
        """Back up, then archive every staged download. A file whose backup fails is not archived."""
        for temp, remote, key, result in staged:
            try:
                if self._stop.is_set():
                    continue   # not recorded: downloaded again by the next sync
                backup = self._backup_new(temp, remote.name, result.sha256, result.bytes_received, record, notes)
                if backup is None:
                    continue
                self._store(temp, remote, key, result.sha256, result.bytes_received, record, backup)
            finally:
                temp.unlink(missing_ok=True)

    def _backup_new(self, temp: Path, name: str, sha256: str, size: int, record: dict,
                    notes: list[str]) -> dict | None:
        """The backup record for the file entry, or None if it must not be processed (required, failed)."""
        guard = self._raw_backup
        if guard is None:
            return {}
        if not guard.required():
            record["backup_bypass"] += 1
            return {"status": BACKUP_BYPASS}
        attempted_at = _utc_now()
        try:
            result = guard.backup(temp, name, sha256, size, attempted_at)
        except RawBackupError as exc:
            record["backup_failed"] += 1
            notes.append(str(exc)[:300])
            logger.warning("Swisseldex: raw backup of %s failed: %s", name, exc)
            with self._lock:
                failures = self._state["backup_failures"]
                failures[name] = {"name": name, "sha256": sha256, "size": size, "run_id": record["id"],
                                  "attempted_at": _iso(attempted_at), "message": str(exc)[:500]}
                if len(failures) > _MAX_BACKUP_FAILURES:
                    oldest = sorted(failures, key=lambda n: failures[n].get("attempted_at") or "")
                    for stale in oldest[:len(failures) - _MAX_BACKUP_FAILURES]:
                        failures.pop(stale, None)
                self._save()
            return None
        record["backed_up"] += 1
        with self._lock:
            self._state["backup_failures"].pop(name, None)
        return {"status": BACKUP_OK, "path": result.relative, "mount": guard.describe().get("mount", ""),
                "backed_up_at": result.backed_up_at, "verified": True, "reused": result.reused}

    def _backfill_backups(self, record: dict, notes: list[str]) -> None:
        """Required backup: back up received files that have none yet (e.g. received before it was switched on)."""
        guard = self._raw_backup
        if guard is None or not guard.required():
            return
        with self._lock:
            pending = [(sha, dict(f)) for sha, f in self._state["files"].items()
                       if (f.get("raw_backup") or {}).get("status") != BACKUP_OK]
        for sha, entry in pending:
            if self._stop.is_set():
                return
            received_at = _from_iso(entry.get("received_at")) or _utc_now()
            source = self._received_dir / str(entry.get("stored_as", ""))
            try:
                result = guard.backup(source, entry["name"], sha, int(entry.get("size") or 0), received_at)
            except RawBackupError as exc:
                info = {"status": BACKUP_ERROR, "message": str(exc)[:500], "attempted_at": _iso(_utc_now())}
                record["backup_failed"] = record.get("backup_failed", 0) + 1
                notes.append(str(exc)[:300])
            else:
                info = {"status": BACKUP_OK, "path": result.relative, "mount": guard.describe().get("mount", ""),
                        "backed_up_at": result.backed_up_at, "verified": True, "reused": result.reused,
                        "backfilled": True}
                record["backfilled"] = record.get("backfilled", 0) + 1
            with self._lock:
                if sha in self._state["files"]:
                    self._state["files"][sha]["raw_backup"] = info
        if pending:
            with self._lock:
                self._save()

    def _store(self, temp: Path, remote: RemoteFile, key: str | None, sha256: str, size: int, record: dict,
               backup: dict | None = None) -> None:
        with self._lock:
            existing = self._state["files"].get(sha256)
        if existing is not None:
            if backup and backup.get("status") == BACKUP_OK and existing.get("name") == remote.name \
                    and (existing.get("raw_backup") or {}).get("status") != BACKUP_OK:
                with self._lock:
                    existing["raw_backup"] = backup
            if existing.get("name") == remote.name:
                record["known"] += 1
            else:
                record["duplicates"] += 1
                aliases = existing.setdefault("aliases", [])
                if remote.name not in aliases and len(aliases) < 20:
                    aliases.append(remote.name)
        else:
            final = self._received_dir / f"{sha256[:16]}_{remote.name}"
            if not final.exists():
                os.replace(temp, final)
                _fsync_dir(self._received_dir)
            problem = _gzip_problem(final) if remote.name.lower().endswith(".gz") else ""
            with self._lock:
                same_name = any(f.get("name") == remote.name for f in self._state["files"].values())
            note = problem or ("gleicher Dateiname wie eine frühere Datei, anderer Inhalt" if same_name else "")
            entry = {
                "name": remote.name, "sha256": sha256, "size": size,
                "remote_modified": remote.modified, "stored_as": final.name,
                "received_at": _iso(_utc_now()), "run_id": record["id"],
                "status": FILE_NEEDS_REVIEW if problem else FILE_RECEIVED, "note": note,
            }
            if backup:
                entry["raw_backup"] = backup
            with self._lock:
                self._state["files"][sha256] = entry
            record["downloaded"] += 1
            if problem:
                record["review"] += 1
        with self._lock:
            if key is not None:
                self._state["seen"][key] = sha256
            self._save()

    def _skip(self, remote: RemoteFile, reason: str, record: dict) -> None:
        """Not downloaded, kept visible as "needs review" - never silently ignored."""
        with self._lock:
            self._state["skipped"][remote.name] = {
                "reason": reason, "size": remote.size, "remote_modified": remote.modified,
                "noticed_at": _iso(_utc_now()),
            }
            self._save()
        record["review"] += 1
