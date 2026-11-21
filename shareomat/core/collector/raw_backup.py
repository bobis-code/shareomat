# -*- coding: utf-8 -*-
"""
File: shareomat/core/collector/raw_backup.py

Purpose:
    Write-once backup of the original files received from Swisseldex onto a
    network storage (e.g. OpenMediaVault, mounted by Home Assistant as
    network storage with usage "Share" under /share/<name>) BEFORE anything
    else reads or processes them (ADR-0010).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    * Target: <network mount>/<subfolder>/<YYYY-MM>/<original name>. The
      month comes from the date prefix of the name (YYYYMMDD_...), so a
      repeated delivery always lands in the same folder; names without that
      prefix use the local month of reception.
    * Only exclusive creates (O_CREAT | O_EXCL, atomic on the server for SMB
      and NFS). Nothing in this module renames, replaces, truncates or
      deletes a file; tests/test_raw_backup.py checks the source for it.
    * A copy counts as complete only when its proof file "<name>.sha256"
      exists and matches. A data file without a proof file is incomplete:
      if it is exactly the beginning of the original (an interrupted copy),
      the missing bytes are appended and the whole file is verified again -
      nothing is overwritten or deleted, nobody has to act. Any other file
      under that name is never touched; the error message says step by step
      what to do (fail closed, no silent rename).
    * The copy is read back after fsync and compared by SHA-256 and size.
      Assumption / needs verification: with the CIFS option cache=strict the
      read may be served from the client cache. posix_fadvise(DONTNEED) asks
      the kernel to drop it first; this is not a proof that the bytes were
      read from the server.
    * Only real network mounts (cifs, smb3, nfs, nfs4) directly below the
      share root are accepted. There is no fallback to local storage: a
      missing mount is an error.
    * Software protection only: the storage user may technically still
      change or delete files. Server-side protection (e.g. Samba vfs_worm or
      snapshots) is configured on the storage, not here.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from shareomat.leg_const import RAW_BACKUP_MODE_OFF, RAW_BACKUP_MODE_REQUIRED

SHARE_ROOT = Path("/share")
MOUNTS_FILE = Path("/proc/self/mounts")
NETWORK_FS_TYPES = frozenset({"cifs", "smb3", "nfs", "nfs4"})
AUTOMOUNT_FS_TYPE = "autofs"
DEFAULT_SUBDIR = "swisseldex-rohdaten"
PROOF_SUFFIX = ".sha256"
MAX_BACKUP_BYTES = 64 * 1024 * 1024   # above the receiver's 50 MB download limit

_LOCAL_TZ = ZoneInfo("Europe/Zurich")
_SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,199}$")
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_MOUNT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DATE_PREFIX = re.compile(r"^(20\d{2})(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])_")
_CHUNK = 1024 * 1024
_MAX_PROOF_BYTES = 4096
_LOCK = threading.Lock()   # one backup at a time in this process


class RawBackupError(Exception):
    """The original could not be backed up and verified; the file must not be processed."""


@dataclass(frozen=True)
class NetworkMount:
    name: str       # folder name below the share root, e.g. "omv"
    path: Path
    fstype: str     # "cifs", "nfs4", ... or "autofs" while not yet mounted
    source: str

    @property
    def mounted(self) -> bool:
        return self.fstype in NETWORK_FS_TYPES


@dataclass(frozen=True)
class BackupTarget:
    mount: NetworkMount
    subdir: str
    base: Path
    still_mounted: Callable[[], bool]   # checked again right before anything is written


@dataclass(frozen=True)
class BackupResult:
    path: Path
    proof: Path
    relative: str        # "<subdir>/<YYYY-MM>/<name>" below the mount
    backed_up_at: str
    reused: bool         # the identical, verified copy existed already
    resumed: bool = False   # an interrupted copy was completed


# ── Private helpers ───────────────────────────────────────────────────────────


def _unescape(field: str) -> str:
    """/proc/self/mounts escapes space, tab, newline and backslash as octal."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def _read_mounts(share_root: Path, mounts_file: Path) -> dict[str, NetworkMount]:
    """Network (and automount) entries whose mount point is a direct child of the share root."""
    try:
        text = mounts_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    found: dict[str, NetworkMount] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        source, point, fstype = _unescape(parts[0]), Path(_unescape(parts[1])), parts[2]
        if point.parent != share_root or not _MOUNT_NAME.match(point.name):
            continue
        if fstype not in NETWORK_FS_TYPES and fstype != AUTOMOUNT_FS_TYPE:
            continue
        previous = found.get(point.name)
        if previous is not None and previous.mounted and fstype == AUTOMOUNT_FS_TYPE:
            continue   # autofs placeholder listed next to the real mount
        found[point.name] = NetworkMount(point.name, point, fstype, source)
    return found


def _touch_mount(path: Path) -> None:
    """Make an automount (autofs) mount now: listing the folder triggers it. Errors are left to the caller."""
    try:
        with os.scandir(path) as entries:
            next(entries, None)
    except OSError:
        pass


def _month_folder(name: str, received_at: datetime) -> str:
    match = _DATE_PREFIX.match(name)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    moment = received_at if received_at.tzinfo else received_at.replace(tzinfo=timezone.utc)
    return moment.astimezone(_LOCAL_TZ).strftime("%Y-%m")


def _sha256_of(path: Path, limit: int = MAX_BACKUP_BYTES) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            size += len(chunk)
            if size > limit:
                raise RawBackupError(f"{path.name}: grösser als {limit} Bytes.")
            digest.update(chunk)
    return digest.hexdigest(), size


def _drop_cache(path: Path) -> None:
    """Ask the kernel to forget cached pages, so the read-back goes to the storage if it can."""
    if not hasattr(os, "posix_fadvise"):
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.posix_fadvise(fd, 0, 0, os.POSIX_FADV_DONTNEED)
    except OSError:
        pass
    finally:
        os.close(fd)


def _create_exclusive(path: Path) -> int:
    """Create a new file; FileExistsError if anything with that name exists (never truncates)."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    return os.open(path, flags, 0o644)


def _write_all(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError("write returned no progress")
        view = view[written:]


def _copy_into_new_file(source: Path, target: Path) -> None:
    fd = _create_exclusive(target)
    try:
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                _write_all(fd, chunk)
        os.fsync(fd)
    finally:
        os.close(fd)


def _same_beginning(existing: Path, source: Path, length: int) -> bool:
    """Whether the first `length` bytes of source equal the whole existing file."""
    with existing.open("rb") as left, source.open("rb") as right:
        remaining = length
        while remaining > 0:
            a = left.read(min(_CHUNK, remaining))
            b = right.read(min(_CHUNK, remaining))
            if not a or a != b:
                return False
            remaining -= len(a)
        return left.read(1) == b""


def _append_rest(source: Path, target: Path, offset: int) -> None:
    """Complete an interrupted copy: append source[offset:] (O_APPEND, never truncates or rewrites)."""
    flags = os.O_WRONLY | os.O_APPEND | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags)
    try:
        with source.open("rb") as handle:
            handle.seek(offset)
            for chunk in iter(lambda: handle.read(_CHUNK), b""):
                _write_all(fd, chunk)
        os.fsync(fd)
    finally:
        os.close(fd)


def _foreign_file_text(name: str, target: BackupTarget, relative: str) -> str:
    return (
        f"{name}: Auf dem Netzwerkspeicher «{target.mount.name}» liegt unter «{relative}» eine Datei, die keine "
        "gültige Sicherung dieser Lieferung ist (keine Prüfdatei, anderer Inhalt). Shareomat überschreibt und löscht "
        "sie nicht. So lösen: 1. Diese Datei auf dem Speicher löschen (sie ist keine fertige Sicherung). "
        "2. Unter «Externe Daten» auf «Jetzt synchronisieren» klicken. Shareomat holt die Lieferung erneut und "
        "sichert sie. Bis dahin bleibt sie unverarbeitet, es geht nichts verloren."
    )


def _other_version_text(name: str, target: BackupTarget, relative: str) -> str:
    return (
        f"{name}: Unter «{relative}» auf dem Netzwerkspeicher «{target.mount.name}» ist bereits eine andere Lieferung "
        "mit genau diesem Dateinamen gesichert (andere Prüfsumme). Beide sind Originale des Netzbetreibers. Die "
        "gesicherte bleibt unverändert, die neue wird nicht verarbeitet. So lösen: Beim Netzbetreiber bzw. bei "
        "Swisseldex nachfragen, warum dieselbe Datei mit anderem Inhalt geliefert wurde. Nichts löschen. Die neue "
        "Lieferung bleibt auf Swisseldex und wird bei jeder Synchronisation erneut versucht."
    )


def _proof_text(name: str, sha256: str, size: int, received_at: datetime, backed_up_at: str) -> bytes:
    payload = {
        "datei": name,
        "sha256": sha256,
        "groesse_bytes": size,
        "empfangen": received_at.astimezone(timezone.utc).isoformat(),
        "gesichert": backed_up_at,
        "geprueft": "SHA-256 der Kopie nach dem Schreiben erneut gelesen und verglichen",
        "quelle": "Swisseldex Datahub",
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _read_proof(proof: Path) -> dict:
    try:
        if proof.stat().st_size > _MAX_PROOF_BYTES:
            raise RawBackupError(f"{proof.name}: Prüfdatei unerwartet gross.")
        data = json.loads(proof.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RawBackupError(f"{proof.name}: Prüfdatei nicht lesbar ({exc}). Bitte auf dem Speicher prüfen.") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sha256"), str):
        raise RawBackupError(f"{proof.name}: Prüfdatei hat ein unbekanntes Format. Bitte auf dem Speicher prüfen.")
    return data


def _write_proof(proof: Path, text: bytes) -> None:
    fd = _create_exclusive(proof)
    try:
        _write_all(fd, text)
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify_copy(path: Path, sha256: str, size: int) -> None:
    _drop_cache(path)
    try:
        copied_sha, copied_size = _sha256_of(path)
    except OSError as exc:
        raise RawBackupError(f"{path.name}: Kopie auf dem Speicher nicht lesbar ({exc.strerror or exc}).") from exc
    if copied_size != size or copied_sha != sha256:
        raise RawBackupError(
            f"{path.name}: Die Kopie auf dem Speicher weicht vom Original ab (Grösse {copied_size} statt {size} "
            "oder andere Prüfsumme) und gilt nicht als Sicherung. So lösen: 1. Diese Datei auf dem Speicher löschen. "
            "2. Unter «Externe Daten» auf «Jetzt synchronisieren» klicken. Tritt das wiederholt auf, den Speicher "
            "selbst prüfen (Platte, Netzwerk). Shareomat löscht und überschreibt dort nichts."
        )


def _backup_locked(source: Path, name: str, sha256: str, size: int, target: BackupTarget,
                   received_at: datetime, now: datetime | None) -> BackupResult:
    month = _month_folder(name, received_at)
    folder = target.base / month
    if not target.still_mounted():
        raise RawBackupError(f"Netzwerkspeicher «{target.mount.name}» ist nicht mehr eingebunden.")
    folder.mkdir(parents=True, exist_ok=True)
    if not folder.resolve().is_relative_to(target.mount.path.resolve()):
        raise RawBackupError(f"Zielordner {folder} liegt nicht auf dem Netzwerkspeicher (Verknüpfung?).")
    data_path = folder / name
    proof_path = folder / (name + PROOF_SUFFIX)
    relative = f"{target.subdir}/{month}/{name}"

    if proof_path.exists():
        proof = _read_proof(proof_path)
        if proof["sha256"] != sha256:
            raise RawBackupError(_other_version_text(name, target, relative))
        _verify_copy(data_path, sha256, size)
        return BackupResult(data_path, proof_path, relative, str(proof.get("gesichert", "")), reused=True)

    if not target.still_mounted():
        raise RawBackupError(f"Netzwerkspeicher «{target.mount.name}» ist nicht mehr eingebunden.")
    resumed = False
    try:
        _copy_into_new_file(source, data_path)
    except FileExistsError:
        existing_size = data_path.stat().st_size
        if existing_size > size or not _same_beginning(data_path, source, existing_size):
            raise RawBackupError(_foreign_file_text(name, target, relative)) from None
        if existing_size < size:
            # An interrupted copy of exactly these bytes: complete it instead of asking anybody to act.
            try:
                _append_rest(source, data_path, existing_size)
            except OSError as exc:
                raise RawBackupError(
                    f"{name}: Fortsetzen der Kopie fehlgeschlagen ({exc.strerror or exc}). Shareomat versucht es bei "
                    "der nächsten Synchronisation automatisch erneut."
                ) from exc
            resumed = True
        # existing_size == size: same bytes written before, only the proof is missing (restart in between).
    except OSError as exc:
        raise RawBackupError(
            f"{name}: Kopie auf den Netzwerkspeicher abgebrochen ({exc.strerror or exc}). Shareomat setzt sie bei der "
            "nächsten Synchronisation automatisch fort."
        ) from exc

    _verify_copy(data_path, sha256, size)
    backed_up_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0).isoformat()
    text = _proof_text(name, sha256, size, received_at, backed_up_at)
    try:
        _write_proof(proof_path, text)
    except FileExistsError:
        if _read_proof(proof_path)["sha256"] != sha256:
            raise RawBackupError(f"{name}: Eine andere Prüfdatei ist gleichzeitig entstanden; bitte prüfen.") from None
    except OSError as exc:
        raise RawBackupError(f"{name}: Prüfdatei konnte nicht geschrieben werden ({exc.strerror or exc}).") from exc
    if _read_proof(proof_path)["sha256"] != sha256:
        raise RawBackupError(f"{name}: Prüfdatei nach dem Schreiben nicht korrekt lesbar; bitte prüfen.")
    return BackupResult(data_path, proof_path, relative, backed_up_at, reused=False, resumed=resumed)


# ── Public functions ──────────────────────────────────────────────────────────


def validate_subdir(subdir: str) -> str:
    """The subfolder below the mount: 1-3 plain segments ("a" or "a/b"), no "..", no absolute path."""
    text = (subdir or "").strip().replace("\\", "/")
    parts = [p for p in text.strip("/").split("/") if p] if not text.startswith("/") and ":" not in text else []
    if not parts or len(parts) > 3 or not all(_SAFE_SEGMENT.match(p) and p not in (".", "..") for p in parts):
        raise RawBackupError(
            "Unterordner ungültig: 1 bis 3 Ordnernamen aus Buchstaben, Ziffern, Punkt, Bindestrich oder "
            "Unterstrich, getrennt durch «/»."
        )
    return "/".join(parts)


def network_mounts(share_root: Path = SHARE_ROOT, mounts_file: Path = MOUNTS_FILE) -> list[NetworkMount]:
    """Network storages directly below the share root (also not yet mounted automounts), by name."""
    return sorted(_read_mounts(share_root, mounts_file).values(), key=lambda m: m.name)


def resolve_target(mount_name: str, subdir: str, *, share_root: Path = SHARE_ROOT,
                   mounts_file: Path = MOUNTS_FILE) -> BackupTarget:
    """The verified target folder, or RawBackupError. Mounts an automount first; never falls back to local disk."""
    if not mount_name or not _MOUNT_NAME.match(mount_name):
        raise RawBackupError("Kein Netzwerkspeicher für die Rohdatensicherung gewählt (Einstellungen).")
    clean_subdir = validate_subdir(subdir)
    _touch_mount(share_root / mount_name)
    mount = _read_mounts(share_root, mounts_file).get(mount_name)
    if mount is None or not mount.mounted:
        raise RawBackupError(
            f"Netzwerkspeicher «{mount_name}» ist nicht eingebunden (nicht erreichbar oder in Home Assistant "
            "nicht als Freigabe eingerichtet). Keine Sicherung auf den lokalen Speicher."
        )
    def still_mounted() -> bool:
        current = _read_mounts(share_root, mounts_file).get(mount_name)
        return current is not None and current.mounted

    return BackupTarget(mount, clean_subdir, mount.path / clean_subdir, still_mounted)


def backup_file(source: Path, name: str, sha256: str, size: int, target: BackupTarget, *,
                received_at: datetime, now: datetime | None = None) -> BackupResult:
    """Copy the original bytes of `source` write-once to the target and verify them. Raises RawBackupError."""
    if not _SAFE_NAME.match(name) or name.endswith(PROOF_SUFFIX):
        raise RawBackupError(f"Dateiname nicht zulässig für die Sicherung: {name!r}")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256 or ""):
        raise RawBackupError(f"{name}: ungültige Prüfsumme.")
    if size < 0 or size > MAX_BACKUP_BYTES:
        raise RawBackupError(f"{name}: Grösse {size} Bytes ausserhalb der Grenze.")
    try:
        source_sha, source_size = _sha256_of(source)
    except OSError as exc:
        raise RawBackupError(f"{name}: Quelldatei nicht lesbar ({exc.strerror or exc}).") from exc
    if source_sha != sha256 or source_size != size:
        raise RawBackupError(f"{name}: Die Quelldatei entspricht nicht mehr dem empfangenen Original.")
    with _LOCK:
        try:
            return _backup_locked(source, name, sha256, size, target, received_at, now)
        except RawBackupError:
            raise
        except OSError as exc:
            raise RawBackupError(
                f"{name}: Netzwerkspeicher nicht erreichbar oder nicht beschreibbar ({exc.strerror or exc})."
            ) from exc


TEST_FOLDER = "verbindungstest"
MIN_FREE_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class CheckStep:
    label: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class StorageCheck:
    steps: tuple[CheckStep, ...]
    checked_at: str
    test_file: str        # relative to the mount; stays there (Shareomat never deletes on the storage)

    @property
    def ok(self) -> bool:
        return bool(self.steps) and all(step.ok for step in self.steps)


def check_storage(target: BackupTarget, *, now: datetime | None = None) -> StorageCheck:
    """Prove on the real storage what the backup relies on, with one small new file in <subfolder>/verbindungstest/.

    Steps stop at the first failure. The test file is never deleted by Shareomat.
    """
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    steps: list[CheckStep] = [CheckStep("Netzwerkspeicher eingebunden", True,
                                        f"{target.mount.fstype}, {target.mount.source}")]
    folder = target.base / TEST_FOLDER
    name = f"test-{moment.strftime('%Y%m%dT%H%M%SZ')}-{os.urandom(3).hex()}.txt"
    path = folder / name
    relative = f"{target.subdir}/{TEST_FOLDER}/{name}"
    content = (f"Shareomat Speichertest {moment.isoformat()}\nDiese Datei darf gelöscht werden.\n"
               f"Zufall: {os.urandom(16).hex()}\n").encode("utf-8")
    expected = hashlib.sha256(content).hexdigest()

    def finish() -> StorageCheck:
        return StorageCheck(tuple(steps), moment.isoformat(), relative)

    try:
        if not target.still_mounted():
            raise RawBackupError("nicht mehr eingebunden")
        folder.mkdir(parents=True, exist_ok=True)
        if not folder.resolve().is_relative_to(target.mount.path.resolve()):
            raise RawBackupError("Ordner liegt nicht auf dem Netzwerkspeicher (Verknüpfung?)")
        steps.append(CheckStep("Ordner anlegen", True, str(folder)))
    except (OSError, RawBackupError) as exc:
        steps.append(CheckStep("Ordner anlegen", False, str(getattr(exc, "strerror", None) or exc)))
        return finish()

    try:
        fd = _create_exclusive(path)
        try:
            _write_all(fd, content)
            os.fsync(fd)
        finally:
            os.close(fd)
        steps.append(CheckStep("Neue Datei schreiben", True, relative))
    except OSError as exc:
        steps.append(CheckStep("Neue Datei schreiben", False, str(exc.strerror or exc)))
        return finish()

    try:
        _verify_copy(path, expected, len(content))
        steps.append(CheckStep("Zurücklesen und SHA-256 vergleichen", True, expected[:16] + "…"))
    except RawBackupError as exc:
        steps.append(CheckStep("Zurücklesen und SHA-256 vergleichen", False, str(exc)))
        return finish()

    try:
        fd = _create_exclusive(path)
    except FileExistsError:
        steps.append(CheckStep("Überschreiben wird verhindert", True, "exklusives Anlegen einer vorhandenen Datei abgelehnt"))
    except OSError as exc:
        steps.append(CheckStep("Überschreiben wird verhindert", False, f"unerwarteter Fehler: {exc.strerror or exc}"))
        return finish()
    else:
        os.close(fd)   # nothing written: the content stays as it was
        steps.append(CheckStep("Überschreiben wird verhindert", False,
                               "Der Speicher hat das exklusive Anlegen einer vorhandenen Datei zugelassen."))
        return finish()

    if hasattr(os, "statvfs"):
        try:
            stats = os.statvfs(folder)
            free = stats.f_bavail * stats.f_frsize
        except OSError as exc:
            steps.append(CheckStep("Freier Platz", False, str(exc.strerror or exc)))
            return finish()
        steps.append(CheckStep("Freier Platz", free >= MIN_FREE_BYTES, f"{free / 1024 ** 3:.1f} GB frei"))
    return finish()


class RawBackupGuard:
    """What the Swisseldex receiver asks: is the backup required, and back up one file.

    `settings` returns (mode, mount name, subfolder) and is read on every
    call, so a change on the settings page applies to the next file.
    """

    def __init__(self, settings: Callable[[], tuple[str, str, str]], *,
                 share_root: Path = SHARE_ROOT, mounts_file: Path = MOUNTS_FILE) -> None:
        self._settings = settings
        self._share_root = share_root
        self._mounts_file = mounts_file

    def mode(self) -> str:
        mode = self._settings()[0]
        return mode if mode in (RAW_BACKUP_MODE_OFF, RAW_BACKUP_MODE_REQUIRED) else RAW_BACKUP_MODE_REQUIRED

    def required(self) -> bool:
        return self.mode() == RAW_BACKUP_MODE_REQUIRED

    def describe(self) -> dict:
        """Mode and target as plain data for the status card (no I/O on the storage)."""
        mode, mount, subdir = self._settings()
        return {"mode": self.mode(), "mount": mount, "subdir": subdir,
                "target": str(self._share_root / mount / subdir) if mount and subdir else ""}

    def backup(self, source: Path, name: str, sha256: str, size: int, received_at: datetime) -> BackupResult:
        _, mount, subdir = self._settings()
        target = resolve_target(mount, subdir, share_root=self._share_root, mounts_file=self._mounts_file)
        return backup_file(source, name, sha256, size, target, received_at=received_at)
