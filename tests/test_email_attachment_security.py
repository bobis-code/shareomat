# -*- coding: utf-8 -*-
"""Security regressions for MIME attachment storage; all mail and files are synthetic."""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import shareomat.core.collector.leg_email_importer as mail


def _message(filename="readings.csv", payload=b"synthetic"):
    msg = MIMEMultipart()
    msg["From"] = "sender@example.invalid"
    part = MIMEApplication(payload)
    part.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(part)
    return msg.as_bytes()


def _connection(messages):
    conn = MagicMock()

    def uid(command, *args):
        if command == "search":
            return "OK", [b" ".join(str(uid).encode() for uid in messages)]
        if args[1] == "(RFC822.SIZE)":
            return "OK", [f"1 (UID {args[0]} RFC822.SIZE {len(messages[int(args[0])])})".encode()]
        return "OK", [(b"BODY", messages[int(args[0])])]

    conn.uid.side_effect = uid
    conn.response.return_value = ("UIDVALIDITY", [b"1"])
    conn.select.return_value = ("OK", [b"0"])
    return conn


def _importer(tmp_path):
    return mail.EmailImporterThread(
        "mail.example.invalid", 993, "inbox@example.invalid", "synthetic-password",
        "INBOX", [], tmp_path / "inbox", tmp_path / "state",
    )


def _symlink(link, target, *, directory=False):
    try:
        link.symlink_to(target, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"OS does not allow creating this symlink: {exc}")


@pytest.mark.parametrize("filename", [
    "../escaped.csv", "../../escaped.xml", "..\\escaped.xlsx",
    "C:\\external\\escaped.csv", "\\\\server\\share\\escaped.csv",
    "sub/folder.csv", "CON.csv", "weird\x00name.csv", "line\nbreak.csv", "über & <x>.CSV",
])
def test_mime_names_never_determine_the_storage_path(tmp_path, filename, monkeypatch):
    # Bound even the vulnerable implementation's path probes to synthetic storage.
    # In particular, a MIME UNC path must never cause an actual network lookup.
    real_exists = Path.exists
    allowed = os.path.normcase(os.path.abspath(tmp_path)) + os.sep
    inbox = tmp_path / "inbox"
    untrusted_target = inbox / filename

    def local_exists(path):
        candidate = os.path.normcase(os.path.abspath(path))
        if path == untrusted_target and not candidate.startswith(allowed):
            raise AssertionError("MIME name caused a path probe outside the synthetic test directory")
        return real_exists(path)

    monkeypatch.setattr(Path, "exists", local_exists)
    dest = mail._save_attachment(inbox, 5, filename, b"complete-content")

    assert dest.parent == inbox
    assert re.fullmatch(r"email-5-[0-9a-f]{32}\.(csv|xml|xlsx)", dest.name)
    assert dest.read_bytes() == b"complete-content"
    assert list(inbox.iterdir()) == [dest]
    assert not (tmp_path / "escaped.csv").exists()


def test_absolute_mime_path_cannot_create_an_external_file(tmp_path):
    outside = tmp_path / "outside.csv"
    dest = mail._save_attachment(tmp_path / "inbox", 1, str(outside), b"new")
    assert not outside.exists()
    assert dest.parent == tmp_path / "inbox"
    assert dest.read_bytes() == b"new"


def test_absolute_mime_path_cannot_overwrite_an_existing_file(tmp_path):
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"original")
    mail._save_attachment(tmp_path / "inbox", 1, str(outside), b"new")
    assert outside.read_bytes() == b"original"


def test_repeated_names_and_uids_never_overwrite_files(tmp_path):
    original = tmp_path / "readings.csv"
    original.write_bytes(b"original")
    results = [mail._save_attachment(tmp_path, 5, "readings.csv", value) for value in (b"first", b"second", b"third")]
    assert len(set(results)) == 3
    assert [p.read_bytes() for p in results] == [b"first", b"second", b"third"]
    assert original.read_bytes() == b"original"


def test_generated_name_collision_is_retried_without_overwriting(tmp_path, monkeypatch):
    existing = tmp_path / f"email-5-{'a' * 32}.csv"
    existing.write_bytes(b"original")
    tokens = iter(["a" * 32, "b" * 32])
    # Import here so the test also runs against the original vulnerable implementation.
    import secrets
    monkeypatch.setattr(secrets, "token_hex", lambda n: next(tokens))

    dest = mail._save_attachment(tmp_path, 5, "readings.csv", b"new")

    assert dest.name == f"email-5-{'b' * 32}.csv"
    assert existing.read_bytes() == b"original"
    assert dest.read_bytes() == b"new"


def test_persistent_collisions_fail_without_overwriting_or_leaving_staging(tmp_path, monkeypatch):
    import secrets
    existing = tmp_path / f"email-5-{'a' * 32}.csv"
    existing.write_bytes(b"original")
    monkeypatch.setattr(secrets, "token_hex", lambda n: "a" * 32)
    with pytest.raises(FileExistsError):
        mail._save_attachment(tmp_path, 5, "readings.csv", b"new")
    assert list(tmp_path.iterdir()) == [existing]
    assert existing.read_bytes() == b"original"


def test_partial_write_failure_never_exposes_a_supported_inbox_file(tmp_path, monkeypatch):
    real_fdopen = mail.os.fdopen

    class BrokenWriter:
        def __init__(self, fd, mode):
            self.file = real_fdopen(fd, mode)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.file.close()

        def write(self, content):
            self.file.write(content[:2])
            self.file.flush()
            assert not list(tmp_path.glob("*.csv"))
            raise OSError("synthetic disk-full error")

    monkeypatch.setattr(mail.os, "fdopen", BrokenWriter)
    with pytest.raises(OSError, match="disk-full"):
        mail._save_attachment(tmp_path, 5, "readings.csv", b"complete")
    assert list(tmp_path.iterdir()) == []


def test_reparse_destination_is_rejected_before_any_payload_write(tmp_path, monkeypatch):
    inbox = tmp_path / "inbox"
    outside = tmp_path / "outside"
    inbox.mkdir()
    outside.mkdir()
    real_resolve = Path.resolve

    def redirected(path, *args, **kwargs):
        return outside if path == inbox else real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected)
    with pytest.raises(OSError, match="symlinks or junctions"):
        mail._save_attachment(inbox, 5, "readings.csv", b"new")
    assert not list(inbox.iterdir()) and not list(outside.iterdir())


@pytest.mark.parametrize("dangling", [False, True])
def test_existing_symlink_is_neither_followed_nor_replaced(tmp_path, dangling):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outside = tmp_path / "outside.csv"
    if not dangling:
        outside.write_bytes(b"original")
    trap = inbox / "readings.csv"
    _symlink(trap, outside)

    dest = mail._save_attachment(inbox, 5, "readings.csv", b"new")

    assert trap.is_symlink()
    assert dest != trap and dest.read_bytes() == b"new"
    assert outside.read_bytes() == b"original" if not dangling else not outside.exists()


def test_inbox_symlink_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    inbox = tmp_path / "inbox"
    _symlink(inbox, outside, directory=True)
    with pytest.raises(OSError):
        mail._save_attachment(inbox, 5, "readings.csv", b"new")
    assert list(outside.iterdir()) == []


def test_generated_name_symlink_is_a_collision(tmp_path, monkeypatch):
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"original")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    trap = inbox / f"email-5-{'a' * 32}.csv"
    _symlink(trap, outside)
    import secrets
    tokens = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(secrets, "token_hex", lambda n: next(tokens))
    dest = mail._save_attachment(inbox, 5, "readings.csv", b"new")
    assert dest != trap and trap.is_symlink()
    assert outside.read_bytes() == b"original"


def test_parallel_saves_of_the_same_name_keep_every_content(tmp_path):
    def save(index):
        return mail._save_attachment(tmp_path, 5, "readings.csv", str(index).encode())
    with ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(save, range(8)))
    assert len(set(paths)) == 8
    assert {path.read_bytes() for path in paths} == {str(i).encode() for i in range(8)}


def test_failed_message_does_not_block_later_messages_and_is_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(mail, "_RETRY_BASE_SECONDS", 0)
    importer = _importer(tmp_path)
    messages = {5: _message(), 6: _message(payload=b"later-message")}
    conn = _connection(messages)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", lambda *args, **kwargs: conn)
    real_save = mail._save_attachment

    def fail_one(inbox, uid, filename, content):
        if uid == 5:
            raise PermissionError("synthetic write failure")
        return real_save(inbox, uid, filename, content)

    monkeypatch.setattr(mail, "_save_attachment", fail_one)
    assert importer._import_new_messages() == 1
    assert mail._load_last_uid(importer._state_dir) == 6
    assert [p.read_bytes() for p in importer._inbox.iterdir()] == [b"later-message"]
    state = json.loads(mail._state_path(importer._state_dir).read_text(encoding="utf-8"))
    assert state["pending_uids"] == [5]

    monkeypatch.setattr(mail, "_save_attachment", real_save)
    assert importer._import_new_messages() == 1
    assert {p.read_bytes() for p in importer._inbox.iterdir()} == {b"synthetic", b"later-message"}
    assert importer._import_new_messages() == 0
    assert conn.logout.call_count == 3


def test_complete_content_is_published_exclusively_and_failure_cleans_staging(tmp_path, monkeypatch):
    real_link = os.link
    contents = []

    def check_link(source, target, **kwargs):
        contents.append(source.read_bytes())
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(mail.os, "link", check_link)
    mail._save_attachment(tmp_path, 5, "readings.csv", b"complete")
    assert contents == [b"complete"]
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    def fail_link(*args, **kwargs):
        raise PermissionError("synthetic publish failure")

    monkeypatch.setattr(mail.os, "link", fail_link)
    with pytest.raises(PermissionError):
        mail._save_attachment(tmp_path, 5, "readings.csv", b"not-published")
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before
