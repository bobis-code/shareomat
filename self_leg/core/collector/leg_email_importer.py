# -*- coding: utf-8 -*-
"""
File: self_leg/core/collector/leg_email_importer.py

Purpose:
    Background thread that polls an IMAP mailbox (e.g. a dedicated Gmail
    inbox) for meter-data attachments and copies them into the engine inbox.
    Lets a grid operator or a community member forward meter data by email
    instead of needing direct inbox/share-folder access.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    The mailbox is treated as read-only, same as the share importer: messages
    are fetched with BODY.PEEK[] so the \\Seen flag is never touched, and
    nothing is ever deleted or moved server-side. "Already seen" tracking
    happens locally via the highest IMAP UID processed so far, persisted in
    data/state/email_import_state.json — IMAP UIDs are unique and strictly
    increasing within one mailbox folder.

    If `allowed_senders` is non-empty, attachments are only accepted from
    messages whose From address matches one of the listed addresses
    (case-insensitive). Leave empty to accept attachments from any sender.

    Accepted attachment extensions: .csv, .xml, .xlsx (same as the inbox
    watcher and share importer). Duplicate downstream processing is still
    guarded by leg_storage's SHA-256 dedup.
"""

from __future__ import annotations

import email
import imaplib
import json
import logging
import os
import tempfile
import threading
from email.message import Message
from email.utils import parseaddr
from pathlib import Path

logger = logging.getLogger(__name__)

_IMPORT_EXTENSIONS = {".csv", ".xml", ".xlsx"}
_STATE_FILENAME = "email_import_state.json"


def _state_path(state_dir: Path) -> Path:
    """Return the full path to the email-import UID state file."""
    return state_dir / _STATE_FILENAME


def _load_last_uid(state_dir: Path) -> int:
    """Read the highest already-processed IMAP UID, or 0 if none recorded yet."""
    path = _state_path(state_dir)
    if not path.exists():
        return 0
    try:
        with path.open(encoding="utf-8") as f:
            return int(json.load(f).get("last_uid", 0))
    except (json.JSONDecodeError, KeyError, ValueError, OSError) as exc:
        logger.warning("Email state file unreadable, starting from UID 0: %s", exc)
        return 0


def _save_last_uid(state_dir: Path, uid: int) -> None:
    """Persist the highest processed IMAP UID atomically (temp file + rename)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    target = _state_path(state_dir)
    fd, tmp_path = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"last_uid": uid}, f)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _sender_allowed(msg: Message, allowed_senders: list[str]) -> bool:
    """Return True if the message's From address is permitted (or no allowlist is set)."""
    if not allowed_senders:
        return True
    _, address = parseaddr(msg.get("From", ""))
    return address.lower() in allowed_senders


def _extract_attachments(msg: Message) -> list[tuple[str, bytes]]:
    """Return (filename, content) pairs for attachments with a supported extension."""
    attachments: list[tuple[str, bytes]] = []
    for part in msg.walk():
        filename = part.get_filename()
        if not filename:
            continue
        if Path(filename).suffix.lower() not in _IMPORT_EXTENSIONS:
            continue
        payload = part.get_payload(decode=True)
        if payload:
            attachments.append((filename, payload))
    return attachments


def _save_attachment(inbox_path: Path, uid: int, filename: str, content: bytes) -> Path:
    """Write an attachment to the inbox, prefixing with the UID if the name collides."""
    inbox_path.mkdir(parents=True, exist_ok=True)
    dest = inbox_path / filename
    if dest.exists():
        dest = inbox_path / f"{uid}_{filename}"
    dest.write_bytes(content)
    return dest


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
        super().__init__(name="self_leg-email-importer", daemon=True)
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._username = username
        self._password = password
        self._folder = folder
        self._allowed_senders = allowed_senders
        self._inbox = inbox_path
        self._state_dir = state_dir
        self._interval = interval
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Signal the thread to stop at the next sleep boundary."""
        self._stop_event.set()

    def _import_new_messages(self) -> int:
        """Connect once, copy new attachments, update UID state. Returns count of files copied."""
        last_uid = _load_last_uid(self._state_dir)
        count = 0
        try:
            conn = imaplib.IMAP4_SSL(self._imap_host, self._imap_port)
        except OSError as exc:
            logger.warning("Email importer: could not connect to %s:%s: %s",
                            self._imap_host, self._imap_port, exc)
            return 0

        try:
            conn.login(self._username, self._password)
            conn.select(self._folder, readonly=False)

            typ, data = conn.uid("search", None, f"UID {last_uid + 1}:*")
            if typ != "OK" or not data or not data[0]:
                return 0

            uids = sorted({int(u) for u in data[0].split() if int(u) > last_uid})
            highest_seen = last_uid

            for uid in uids:
                typ, msg_data = conn.uid("fetch", str(uid), "(BODY.PEEK[])")
                if typ != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                if _sender_allowed(msg, self._allowed_senders):
                    for filename, content in _extract_attachments(msg):
                        dest = _save_attachment(self._inbox, uid, filename, content)
                        logger.info("Email importer: saved %s -> inbox", dest.name)
                        count += 1
                else:
                    logger.info("Email importer: skipping message from disallowed sender (UID %d)", uid)

                highest_seen = max(highest_seen, uid)

            if highest_seen > last_uid:
                _save_last_uid(self._state_dir, highest_seen)
        except imaplib.IMAP4.error as exc:
            logger.warning("Email importer: IMAP error: %s", exc)
        finally:
            try:
                conn.logout()
            except Exception:
                pass

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
