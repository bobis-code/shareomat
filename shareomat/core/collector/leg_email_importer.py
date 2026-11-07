# -*- coding: utf-8 -*-
"""
File: shareomat/core/collector/leg_email_importer.py

Purpose:
    Background thread that polls an IMAP mailbox (e.g. a dedicated Gmail
    inbox) for meter-data attachments and copies them into the engine inbox.
    Lets a grid operator or a community member forward meter data by email
    instead of needing direct inbox/share-folder access.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The mailbox is treated as read-only, same as the share importer: messages
    are fetched with BODY.PEEK[] so the \\Seen flag is never touched, and
    nothing is ever deleted or moved server-side. Mailbox identity, UIDVALIDITY,
    a high-water UID, retry diagnostics and attachment checkpoints are persisted
    in data/state/email_import_state.json. UIDs only identify messages within
    one mailbox generation; old diagnostics are archived before a reset.

    If `allowed_senders` is non-empty, attachments are only accepted from
    messages whose From address matches one of the listed addresses
    (case-insensitive). This is NOT sender authentication. Empty still accepts
    any sender for compatibility; the receiving server must enforce trust.

    Accepted attachment extensions: .csv, .xml, .xlsx (same as the inbox
    watcher and share importer). Duplicate downstream processing is still
    guarded by leg_storage's SHA-256 dedup.
    MIME names never become storage paths. Only complete attachments are
    published, with server-generated names and exclusive hard-link creation.
"""

from __future__ import annotations

import hashlib
import imaplib
import json
import logging
import math
import os
import re
import secrets
import shutil
import ssl
import tempfile
import threading
import time
from email import policy
from email.message import Message
from email.parser import BytesParser
from email.utils import getaddresses
from itertools import zip_longest
from pathlib import Path

logger = logging.getLogger(__name__)

_IMPORT_EXTENSIONS = {".csv", ".xml", ".xlsx"}
_STATE_FILENAME = "email_import_state.json"
_MAX_MESSAGE_BYTES = 100 * 1024 * 1024
_MAX_ATTACHMENT_BYTES = 64 * 1024 * 1024
_MAX_TOTAL_ATTACHMENT_BYTES = 64 * 1024 * 1024
_MAX_ATTACHMENTS = 8
_MAX_MIME_PARTS = 64
_MAX_HEADERS = 256
_MAX_HEADER_BYTES = 64 * 1024
_MAX_MESSAGES_PER_POLL = 20
_MAX_FETCH_BYTES_PER_POLL = 128 * 1024 * 1024
_MAX_INBOX_BYTES = 512 * 1024 * 1024
_MAX_INBOX_FILES = 10_000
_MIN_FREE_DISK_BYTES = 256 * 1024 * 1024
_MAX_PENDING_UIDS = 1000
_MAX_STATE_BYTES = 4 * 1024 * 1024
_MAX_HISTORY_BYTES = 16 * 1024 * 1024
_IMAP_TIMEOUT_SECONDS = 30
_MAX_POLL_SECONDS = 60
_RETRY_BASE_SECONDS = 60
_MAX_RETRY_SECONDS = 3600


class EmailImportError(ValueError):
    """A message cannot safely be imported; retain it and expose a retry diagnostic."""


class _PollBudgetReached(EmailImportError):
    """Defer remaining work to the next poll without treating it as corrupt data."""


def _now() -> float:
    return time.time()


def _state_path(state_dir: Path) -> Path:
    """Return the full path to the email-import UID state file."""
    return state_dir / _STATE_FILENAME


def _read_state(state_dir: Path) -> dict:
    """Read bounded, validated state; never silently reset a corrupt cursor."""
    path = _state_path(state_dir)
    try:
        if path.stat().st_size > _MAX_STATE_BYTES:
            raise EmailImportError("Email state exceeds size limit")
        with path.open(encoding="utf-8") as f:
            state = json.load(f)
    except FileNotFoundError:
        return {"last_uid": 0, "pending_uids": [], "failures": {}, "completed": {}}
    if not isinstance(state, dict):
        raise EmailImportError("Invalid email state object")
    state["last_uid"] = int(state.get("last_uid", 0))
    state["pending_uids"] = sorted({int(uid) for uid in state.get("pending_uids", [])})
    if state["last_uid"] < 0 or any(uid <= 0 for uid in state["pending_uids"]):
        raise EmailImportError("Invalid email UID state")
    if len(state["pending_uids"]) > _MAX_PENDING_UIDS:
        raise EmailImportError("Email retry backlog exceeds limit; review state before continuing")
    for field in ("failures", "completed"):
        state.setdefault(field, {})
        if not isinstance(state[field], dict):
            raise EmailImportError(f"Invalid email state field: {field}")
    for failure in state["failures"].values():
        if (not isinstance(failure, dict) or not isinstance(failure.get("retry_at", 0), (int, float))
                or not math.isfinite(failure.get("retry_at", 0))):
            raise EmailImportError("Invalid email retry diagnostic")
    for parts in state["completed"].values():
        if not isinstance(parts, list) or any(not isinstance(part, str) for part in parts):
            raise EmailImportError("Invalid email attachment checkpoint")
    return state


def _load_last_uid(state_dir: Path) -> int:
    """Compatibility helper for the existing state-file interface."""
    return _read_state(state_dir)["last_uid"]


def _load_pending_uids(state_dir: Path) -> set[int]:
    """Old state files have no pending list; failed messages remain retryable."""
    return set(_read_state(state_dir)["pending_uids"])


def _write_state(state_dir: Path, state: dict) -> None:
    """Persist checkpoints atomically without changing IMAP messages or flags."""
    encoded = json.dumps(state).encode("utf-8")
    if len(encoded) > _MAX_STATE_BYTES:
        raise EmailImportError("Email state exceeds size limit")
    state_dir.mkdir(parents=True, exist_ok=True)
    target = _state_path(state_dir)
    fd, tmp_path = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _save_last_uid(state_dir: Path, uid: int, *, pending_uids: set[int] | None = None) -> None:
    """Compatibility helper; mailbox identity is established on the next poll."""
    _write_state(state_dir, {"last_uid": uid, "pending_uids": sorted(pending_uids or ()),
                             "failures": {}, "completed": {}})


def _archive_epoch(state_dir: Path, state: dict) -> None:
    """Keep obsolete UID diagnostics before changing mailbox generations."""
    encoded = json.dumps(state).encode("utf-8")
    history_size = sum(path.stat().st_size for path in state_dir.glob("email-epoch-*.json"))
    if history_size + len(encoded) > _MAX_HISTORY_BYTES:
        raise EmailImportError("Email epoch history quota reached; archive history before mailbox reset")
    state_dir.mkdir(parents=True, exist_ok=True)
    fd, path = tempfile.mkstemp(prefix="email-epoch-", suffix=".json", dir=state_dir)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(encoded)
    except Exception:
        Path(path).unlink(missing_ok=True)
        raise


def _sender_allowed(msg: Message, allowed_senders: list[str]) -> bool:
    """A compatibility From FILTER, never authentication or proof of message origin."""
    if not allowed_senders:
        return True
    authors = getaddresses(msg.get_all("From", []))
    return len(authors) == 1 and bool(authors[0][1]) and authors[0][1].lower() in allowed_senders


def _extract_attachments(msg: Message) -> list[tuple[str, bytes]]:
    """Return (filename, content) pairs for attachments with a supported extension."""
    attachments: list[tuple[str, bytes]] = []
    total = 0
    for index, part in enumerate(msg.walk()):
        if index >= _MAX_MIME_PARTS or part.defects:
            raise EmailImportError("Malformed or excessive MIME structure")
        filename = part.get_filename()
        if not filename:
            continue
        if Path(filename).suffix.lower() not in _IMPORT_EXTENSIONS:
            logger.info("Email importer: attachment outside the supported extension filter, left on server")
            continue
        if len(attachments) >= _MAX_ATTACHMENTS:
            raise EmailImportError("Attachment count limit exceeded")
        payload = part.get_payload(decode=True)
        if not payload or part.defects:
            raise EmailImportError("Empty or malformed attachment")
        if len(payload) > _MAX_ATTACHMENT_BYTES:
            raise EmailImportError("Attachment size limit exceeded")
        total += len(payload)
        if total > _MAX_TOTAL_ATTACHMENT_BYTES:
            raise EmailImportError("Total decoded attachment size limit exceeded")
        attachments.append((filename, payload))
    return attachments


def _parse_message(raw: bytes) -> Message:
    """Bound object creation during parsing, not just after building a MIME tree."""
    count = 0
    header_count = header_bytes = 0

    class BoundedPolicy(policy.Compat32):
        def header_source_parse(self, sourcelines):
            nonlocal header_count, header_bytes
            header_count += 1
            header_bytes += sum(len(line) for line in sourcelines)
            if header_count > _MAX_HEADERS or header_bytes > _MAX_HEADER_BYTES:
                raise EmailImportError("MIME header limit exceeded")
            return super().header_source_parse(sourcelines)

    def factory(*args, **kwargs):
        nonlocal count
        count += 1
        if count > _MAX_MIME_PARTS:
            raise EmailImportError("MIME part limit exceeded")
        return Message(*args, **kwargs)

    if not raw:
        raise EmailImportError("Empty message")
    return BytesParser(_class=factory, policy=BoundedPolicy(raise_on_defect=True)).parsebytes(raw)


def _check_capacity(inbox: Path, size: int) -> None:
    """Backpressure, never deletion: old files remain available to the normal runner."""
    inbox.mkdir(parents=True, exist_ok=True)
    total = count = 0
    with os.scandir(inbox) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                total += entry.stat(follow_symlinks=False).st_size
                count += 1
                if count >= _MAX_INBOX_FILES or total + size > _MAX_INBOX_BYTES:
                    raise EmailImportError("Inbox file/byte quota exceeded; process or archive existing files")
    if size > _MAX_INBOX_BYTES:
        raise EmailImportError("Inbox byte quota exceeded")
    if shutil.disk_usage(inbox).free < size + _MIN_FREE_DISK_BYTES:
        raise EmailImportError("Insufficient free disk space for email attachment")


def _limit_imap_reads(conn, deadline: float) -> None:
    """Reject excessive server literals before allocating them; bound reads and poll time."""
    original = conn.read
    consumed = 0

    def bounded_read(size):
        nonlocal consumed
        if size < 0 or size > _MAX_MESSAGE_BYTES + 1:
            raise imaplib.IMAP4.abort("IMAP literal size exceeds message limit")
        if consumed + size > _MAX_FETCH_BYTES_PER_POLL + _MAX_MESSAGES_PER_POLL * 4096:
            raise imaplib.IMAP4.abort("IMAP literal byte budget exceeded")
        result = bytearray()
        while len(result) < size:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Email poll time budget exceeded")
            conn.sock.settimeout(min(_IMAP_TIMEOUT_SECONDS, remaining))
            block = original(min(64 * 1024, size - len(result)))
            if not block or len(block) > size - len(result):
                raise imaplib.IMAP4.abort("Incomplete IMAP literal")
            consumed += len(block)
            result.extend(block)
        return bytes(result)

    conn.read = bounded_read


def _fetch_message(conn, uid: int, remaining_bytes: int) -> bytes:
    typ, data = conn.uid("fetch", str(uid), "(RFC822.SIZE)")
    metadata = b" ".join(item for item in (data or []) if isinstance(item, bytes))
    match = re.search(rb"\bRFC822.SIZE\s+(\d+)", metadata) if typ == "OK" else None
    if match is None:
        raise EmailImportError("Message size metadata unavailable")
    size = int(match[1])
    if not 0 < size <= _MAX_MESSAGE_BYTES:
        raise EmailImportError(f"Message size {size} exceeds allowed range (max {_MAX_MESSAGE_BYTES})")
    if size > remaining_bytes:
        raise _PollBudgetReached("Email poll byte budget reached; message deferred")
    typ, data = conn.uid("fetch", str(uid), f"(BODY.PEEK[]<0.{size}>)")
    literals = [item[1] for item in (data or []) if isinstance(item, tuple) and len(item) == 2
                and isinstance(item[1], bytes)]
    if typ != "OK" or len(literals) != 1 or len(literals[0]) != size:
        raise EmailImportError("Incomplete or inconsistent message body")
    return literals[0]


def _save_attachment(inbox_path: Path, uid: int, filename: str, content: bytes) -> Path:
    """Publish complete content under an exclusive server-generated name.

    The MIME name supplies only an allowlisted suffix, never a path component.
    A private staging directory keeps partial content out of the inbox scanner.
    Hard-link publication is atomic and fails on existing files AND symlinks;
    never fall back to an overwrite or a visible partially written file.
    Inbox and its parents must be real, access-controlled directories, not
    symlinks/junctions. Local processes must not be allowed to replace them.
    """
    extension = Path(filename).suffix.lower()
    if extension not in _IMPORT_EXTENSIONS:
        raise ValueError("Unsupported email attachment extension")
    inbox_path.mkdir(parents=True, exist_ok=True)
    inbox = Path(os.path.abspath(inbox_path))
    if inbox.resolve(strict=True) != inbox:
        raise OSError("Email inbox must not contain symlinks or junctions")
    identity = inbox.stat()
    with tempfile.TemporaryDirectory(prefix=".email-", dir=inbox) as staging:
        payload = Path(staging) / "payload"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(payload, flags, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        for _ in range(32):
            if inbox.resolve(strict=True) != inbox or not os.path.samestat(identity, inbox.stat()):
                raise OSError("Email inbox changed during attachment storage")
            dest = inbox / f"email-{uid}-{secrets.token_hex(16)}{extension}"
            try:
                os.link(payload, dest, follow_symlinks=False)
            except FileExistsError:
                continue
            return dest
    raise FileExistsError("Could not allocate an exclusive email attachment name")


class EmailImporterThread(threading.Thread):
    """Daemon thread that copies new email attachments from an IMAP mailbox into the inbox."""

    def __init__(
        self,
        imap_host: str,
        imap_port: int,
        username: str,
        password: str,
        folder: str,
        allowed_senders: list[str],
        inbox_path: Path,
        state_dir: Path,
        interval: int = 300,
    ) -> None:
        super().__init__(name="shareomat-email-importer", daemon=True)
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._username = username
        self._password = password
        self._folder = folder
        self._allowed_senders = [address.strip().lower() for address in allowed_senders if address.strip()]
        self._inbox = inbox_path
        self._state_dir = state_dir
        self._interval = max(1, interval)
        self._stop_event = threading.Event()
        self._trust_warned = False

    def stop(self) -> None:
        """Signal the thread to stop at the next sleep boundary."""
        self._stop_event.set()

    def _import_new_messages(self) -> int:
        """One bounded poll; isolate bad messages and keep durable retry/progress state."""
        if not self._trust_warned:
            logger.warning("Email importer: From filtering is not sender authentication. Enforce sender "
                           "verification on the receiving mail server; untrusted Authentication-Results "
                           "headers are not evidence.%s", " Empty allowlist accepts any sender."
                           if not self._allowed_senders else "")
            self._trust_warned = True
        count = 0
        conn = None
        try:
            deadline = time.monotonic() + _MAX_POLL_SECONDS
            state = _read_state(self._state_dir)
            conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port,
                                    ssl_context=ssl.create_default_context(), timeout=_IMAP_TIMEOUT_SECONDS)
            conn.sock.settimeout(_IMAP_TIMEOUT_SECONDS)
            _limit_imap_reads(conn, deadline)
            conn.login(self._username, self._password)
            typ, _ = conn.select(self._folder, readonly=True)
            if typ != "OK":
                raise EmailImportError("IMAP folder could not be selected")
            _, validity = conn.response("UIDVALIDITY")
            generation = validity[0] if validity else None
            if not isinstance(generation, bytes) or not generation.isdigit() or int(generation) <= 0:
                raise EmailImportError("UIDVALIDITY unavailable; refusing to guess mailbox generation")
            generation = generation.decode("ascii")
            mailbox = hashlib.sha256("\0".join((self._imap_host, str(self._imap_port),
                                               self._username, self._folder)).encode()).hexdigest()
            if state.get("uidvalidity") != generation or state.get("mailbox") != mailbox:
                if _state_path(self._state_dir).exists():
                    _archive_epoch(self._state_dir, state)
                    logger.warning("Email importer: mailbox identity/UIDVALIDITY changed or legacy cursor; "
                                   "old diagnostics archived, scanning current mailbox from UID 1")
                state = {"last_uid": 0, "pending_uids": [], "failures": {}, "completed": {},
                         "uidvalidity": generation, "mailbox": mailbox}
                _write_state(self._state_dir, state)

            typ, data = conn.uid("search", None, f"UID {state['last_uid'] + 1}:*")
            if typ != "OK":
                raise EmailImportError("IMAP UID search failed")
            search = data[0] if data and data[0] else b""
            if not isinstance(search, bytes) or len(search) > 1024 * 1024:
                raise EmailImportError("IMAP UID search exceeds response limit")
            pending = set(state["pending_uids"])
            new_uids = sorted({int(uid) for uid in search.split() if int(uid) > state["last_uid"]})
            capacity = max(0, _MAX_PENDING_UIDS - len(pending))
            if new_uids and not capacity:
                logger.warning("Email importer: retry backlog full; new mail retained on server until resolved")
            due = sorted(uid for uid in pending
                         if state["failures"].get(str(uid), {}).get("retry_at", 0) <= _now())
            # Alternate new work and retries so one poison message cannot starve later mail.
            work = [uid for pair in zip_longest(new_uids[:capacity], due) for uid in pair if uid is not None]
            fetched_bytes = 0
            for uid in work[:_MAX_MESSAGES_PER_POLL]:
                if self._stop_event.is_set() or time.monotonic() >= deadline:
                    break
                key = str(uid)
                pending.add(uid)
                state["last_uid"] = max(state["last_uid"], uid)
                state["pending_uids"] = sorted(pending)
                _write_state(self._state_dir, state)
                try:
                    raw = _fetch_message(conn, uid, _MAX_FETCH_BYTES_PER_POLL - fetched_bytes)
                    fetched_bytes += len(raw)
                    msg = _parse_message(raw)
                    if _sender_allowed(msg, self._allowed_senders):
                        attachments = _extract_attachments(msg)  # Validate ALL parts before the first write.
                        if not attachments:
                            logger.info("Email importer: UID %d has no supported attachments, left on server", uid)
                        completed = state["completed"].setdefault(key, [])
                        for index, (filename, content) in enumerate(attachments):
                            token = f"{index}:{hashlib.sha256(content).hexdigest()}:{Path(filename).suffix.lower()}"
                            if token in completed:
                                continue
                            if any(part.startswith(f"{index}:") for part in completed):
                                raise EmailImportError("Attachment changed within the same UID; operator review required")
                            _check_capacity(self._inbox, len(content))
                            dest = _save_attachment(self._inbox, uid, filename, content)
                            logger.info("Email importer: saved %s -> inbox", dest.name)
                            completed.append(token)
                            _write_state(self._state_dir, state)
                            count += 1
                    else:
                        logger.warning("Email importer: UID %d rejected by From filter, left on server", uid)
                    pending.discard(uid)
                    state["failures"].pop(key, None)
                    state["completed"].pop(key, None)
                except _PollBudgetReached as exc:
                    logger.info("Email importer: UID %d deferred: %s", uid, exc)
                    state["pending_uids"] = sorted(pending)
                    _write_state(self._state_dir, state)
                    break
                except Exception as exc:
                    attempts = int(state["failures"].get(key, {}).get("attempts", 0)) + 1
                    delay = min(_MAX_RETRY_SECONDS, _RETRY_BASE_SECONDS * 2 ** min(attempts - 1, 10))
                    reason = f"{type(exc).__name__}: {exc}"[:500]
                    state["failures"][key] = {"reason": reason, "attempts": attempts, "retry_at": _now() + delay}
                    logger.warning("Email importer: UID %d retained for retry in %ss: %s", uid, delay, reason)
                    if isinstance(exc, (imaplib.IMAP4.error, ConnectionError, TimeoutError)):
                        state["pending_uids"] = sorted(pending)
                        _write_state(self._state_dir, state)
                        break
                state["pending_uids"] = sorted(pending)
                _write_state(self._state_dir, state)
        except Exception as exc:
            logger.warning("Email importer: poll stopped safely, retrying next poll: %s: %s", type(exc).__name__, exc)
        finally:
            if conn is not None:
                try:
                    conn.sock.settimeout(5)
                    conn.logout()
                except Exception:
                    logger.debug("Email importer: logout failed after polling error")
                finally:
                    try:
                        conn.shutdown()
                    except Exception:
                        logger.debug("Email importer: connection already closed")

        return count

    def run(self) -> None:
        """Thread main loop: import on startup, then every interval seconds."""
        logger.info(
            "Email importer started — watching %s@%s every %ds",
            self._username, self._imap_host, self._interval,
        )

        copied = self._import_new_messages()
        if copied:
            logger.info("Email importer: %d file(s) copied on startup", copied)

        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._interval)
            if self._stop_event.is_set():
                break
            self._import_new_messages()

        logger.info("Email importer stopped")
