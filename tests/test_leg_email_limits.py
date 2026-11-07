# -*- coding: utf-8 -*-
"""Bounded IMAP ingestion, mailbox generations and per-message recovery."""

from __future__ import annotations

import errno
import json
import ssl
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import Mock

import pytest

import shareomat.core.collector.leg_email_importer as mail


def _message(parts=(b"synthetic",), sender="sender@example.invalid"):
    msg = MIMEMultipart()
    msg["From"] = sender
    msg.attach(MIMEText("Synthetic test message"))
    for index, payload in enumerate(parts):
        part = MIMEApplication(payload)
        part.add_header("Content-Disposition", "attachment", filename=f"test-{index}.csv")
        msg.attach(part)
    return msg.as_bytes()


class FakeIMAP:
    def __init__(self):
        self.messages = {}
        self.sizes = {}
        self.generation = b"10"
        self.calls = []
        self.sock = Mock()
        self.read = Mock(return_value=b"")
        self.logout = Mock()
        self.login = Mock(return_value=("OK", []))
        self.select = Mock(return_value=("OK", [b"0"]))

    def response(self, name):
        return name, [self.generation] if name == "UIDVALIDITY" else [b"100"]

    def uid(self, command, *args):
        self.calls.append((command, *args))
        if command == "search":
            return "OK", [b" ".join(str(uid).encode() for uid in sorted(self.messages))]
        uid = int(args[0])
        value = self.messages.get(uid)
        if args[1] == "(RFC822.SIZE)":
            size = self.sizes.get(uid, len(value) if isinstance(value, bytes) else 1)
            return "OK", [f"1 (UID {uid} RFC822.SIZE {size})".encode()]
        if isinstance(value, Exception):
            raise value
        return "OK", [(f"1 (UID {uid} BODY[] {{{len(value or b'')}}})".encode(), value)]


@pytest.fixture
def mailbox(tmp_path, monkeypatch):
    # Small deterministic limits exercise boundaries without allocating large messages.
    for name, value in {
        "_MAX_MESSAGE_BYTES": 4000, "_MAX_ATTACHMENT_BYTES": 600,
        "_MAX_TOTAL_ATTACHMENT_BYTES": 1000, "_MAX_ATTACHMENTS": 2,
        "_MAX_MIME_PARTS": 8, "_MAX_MESSAGES_PER_POLL": 4,
        "_MAX_FETCH_BYTES_PER_POLL": 8000, "_MAX_INBOX_BYTES": 2000,
        "_MIN_FREE_DISK_BYTES": 0, "_RETRY_BASE_SECONDS": 0,
    }.items():
        monkeypatch.setattr(mail, name, value, raising=False)
    conn = FakeIMAP()
    factory = Mock(return_value=conn)
    monkeypatch.setattr(mail.imaplib, "IMAP4_SSL", factory)
    importer = mail.EmailImporterThread("mail.example.invalid", 993, "test@example.invalid", "synthetic",
                                       "INBOX", [], tmp_path / "inbox", tmp_path / "state")
    return importer, conn, factory


def _contents(importer):
    return sorted(path.read_bytes() for path in importer._inbox.glob("*.csv"))


def _state(importer):
    return json.loads(mail._state_path(importer._state_dir).read_text(encoding="utf-8"))


def test_oversized_message_is_not_downloaded_and_does_not_block_a_good_one(mailbox):
    importer, conn, _ = mailbox
    conn.messages = {1: _message(), 2: _message((b"good",))}
    conn.sizes[1] = 4001
    assert importer._import_new_messages() == 1
    assert not any(call[0] == "fetch" and call[1] == "1" and "BODY" in call[2] for call in conn.calls)
    assert _contents(importer) == [b"good"]
    assert _state(importer)["pending_uids"] == [1]
    assert "size" in _state(importer)["failures"]["1"]["reason"].lower()


@pytest.mark.parametrize("parts", [(b"x" * 601,), (b"x" * 550, b"y" * 550), (b"a", b"b", b"c")])
def test_attachment_limits_reject_the_whole_message_before_any_write(mailbox, parts):
    importer, conn, _ = mailbox
    conn.messages = {1: _message(parts), 2: _message((b"good",))}
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"good"]
    assert _state(importer)["pending_uids"] == [1]


@pytest.mark.parametrize("raw", [b"not an email", b"", _message((b"",)),
    b'From: sender@example.invalid\nContent-Type: multipart/mixed; boundary="missing"\n\ntruncated'])
def test_bad_messages_remain_pending_while_valid_messages_continue(mailbox, raw):
    importer, conn, _ = mailbox
    conn.messages = {1: raw, 2: _message((b"good",))}
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"good"]
    assert _state(importer)["pending_uids"] == [1]


def test_truncated_body_does_not_become_a_success(mailbox):
    importer, conn, _ = mailbox
    conn.messages = {1: _message()}
    conn.sizes[1] = len(conn.messages[1]) + 3
    assert importer._import_new_messages() == 0
    assert _contents(importer) == []
    assert _state(importer)["pending_uids"] == [1]


def test_mime_part_factory_stops_excessive_structure(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    monkeypatch.setattr(mail, "_MAX_MIME_PARTS", 3, raising=False)
    conn.messages = {1: _message((b"a", b"b"))}
    assert importer._import_new_messages() == 0
    assert _contents(importer) == []
    assert _state(importer)["pending_uids"] == [1]


def test_partial_attachment_failure_resumes_without_duplicating_saved_parts(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    conn.messages = {1: _message((b"first", b"second")), 2: _message((b"later",))}
    real_save = mail._save_attachment

    def fail_second(inbox, uid, name, content):
        if content == b"second":
            raise PermissionError("synthetic write failure")
        return real_save(inbox, uid, name, content)

    monkeypatch.setattr(mail, "_save_attachment", fail_second)
    assert importer._import_new_messages() == 2
    assert _contents(importer) == [b"first", b"later"]
    monkeypatch.setattr(mail, "_save_attachment", real_save)
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"first", b"later", b"second"]
    assert _state(importer)["pending_uids"] == []


def test_connection_abort_is_retryable_with_a_new_connection(mailbox):
    importer, conn, factory = mailbox
    conn.messages = {1: mail.imaplib.IMAP4.abort("synthetic disconnect")}
    assert importer._import_new_messages() == 0
    conn.messages[1] = _message((b"recovered",))
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"recovered"]
    assert factory.call_count == 2 and conn.logout.call_count == 2


def test_connection_timeout_does_not_kill_next_poll(mailbox):
    importer, conn, factory = mailbox
    factory.side_effect = [TimeoutError("synthetic timeout"), conn]
    conn.messages = {1: _message((b"recovered",))}
    assert importer._import_new_messages() == 0
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"recovered"]


def test_uidvalidity_change_restarts_at_low_uids_and_preserves_old_diagnostics(mailbox):
    importer, conn, _ = mailbox
    conn.messages = {50: _message((b"old",))}
    assert importer._import_new_messages() == 1
    conn.generation = b"11"
    conn.messages = {1: _message((b"new",))}
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"new", b"old"]
    assert _state(importer)["uidvalidity"] == "11"
    assert list(importer._state_dir.glob("email-epoch-*.json"))


def test_missing_uidvalidity_does_not_guess_a_generation(mailbox):
    importer, conn, _ = mailbox
    conn.generation = None
    conn.messages = {1: _message()}
    assert importer._import_new_messages() == 0
    assert not any(call[0] == "fetch" for call in conn.calls)


def test_legacy_uid_state_is_migrated_without_silently_missing_a_reset(mailbox):
    importer, conn, _ = mailbox
    mail._save_last_uid(importer._state_dir, 50)
    conn.messages = {1: _message((b"migrated",))}
    assert importer._import_new_messages() == 1
    assert _state(importer)["uidvalidity"] == "10"


def test_disk_budget_defers_without_losing_messages_and_recovers(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    monkeypatch.setattr(mail, "_MAX_INBOX_BYTES", 4, raising=False)
    conn.messages = {1: _message((b"12345",))}
    assert importer._import_new_messages() == 0
    assert _contents(importer) == []
    assert _state(importer)["pending_uids"] == [1]
    monkeypatch.setattr(mail, "_MAX_INBOX_BYTES", 2000)
    assert importer._import_new_messages() == 1


def test_poll_message_count_is_bounded_without_skipping_the_remainder(mailbox):
    importer, conn, _ = mailbox
    conn.messages = {uid: _message((str(uid).encode(),)) for uid in range(1, 8)}
    assert importer._import_new_messages() == 4
    assert importer._import_new_messages() == 3
    assert len(_contents(importer)) == 7


def test_poll_byte_budget_defers_and_next_poll_continues(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    raw = _message((b"data",))
    monkeypatch.setattr(mail, "_MAX_FETCH_BYTES_PER_POLL", len(raw), raising=False)
    conn.messages = {1: raw, 2: raw}
    assert importer._import_new_messages() == 1
    assert importer._import_new_messages() == 1


def test_imap_tls_verifies_server_and_has_finite_timeout(mailbox):
    importer, conn, factory = mailbox
    importer._import_new_messages()
    kwargs = factory.call_args.kwargs
    assert kwargs["timeout"] > 0
    assert kwargs["ssl_context"].verify_mode == ssl.CERT_REQUIRED
    assert kwargs["ssl_context"].check_hostname
    conn.select.assert_called_once_with("INBOX", readonly=True)


@pytest.mark.parametrize("code", [errno.EXDEV, errno.EOPNOTSUPP, errno.EACCES])
def test_unsupported_hardlinks_and_permissions_remain_controlled_and_retryable(mailbox, monkeypatch, code):
    importer, conn, _ = mailbox
    conn.messages = {1: _message()}
    real_link = mail.os.link

    def broken(*args, **kwargs):
        raise OSError(code, "synthetic hardlink error")

    monkeypatch.setattr(mail.os, "link", broken)
    assert importer._import_new_messages() == 0
    assert _contents(importer) == []
    assert _state(importer)["pending_uids"] == [1]
    monkeypatch.setattr(mail.os, "link", real_link)
    assert importer._import_new_messages() == 1


def test_staging_is_on_the_inbox_filesystem(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    conn.messages = {1: _message()}
    real_link = mail.os.link

    def checked(source, target, **kwargs):
        assert source.parent.parent == target.parent == importer._inbox
        assert source.stat().st_dev == target.parent.stat().st_dev
        return real_link(source, target, **kwargs)

    monkeypatch.setattr(mail.os, "link", checked)
    assert importer._import_new_messages() == 1


def test_from_and_authentication_results_do_not_prove_sender_identity(mailbox, caplog):
    importer, conn, _ = mailbox
    importer._allowed_senders = ["sender@example.invalid"]
    raw = _message()
    raw = b"Authentication-Results: attacker.example; dkim=pass; spf=pass\n" + raw
    conn.messages = {1: raw}
    # Deliberately compatible: provenance must be enforced by the receiving mail server.
    assert importer._import_new_messages() == 1
    assert "not sender authentication" in caplog.text.lower()


def test_from_filter_rejects_ambiguous_multiple_authors():
    msg = MIMEMultipart()
    msg["From"] = "sender@example.invalid, intruder@example.invalid"
    assert not mail._sender_allowed(msg, ["sender@example.invalid"])


def test_retry_backoff_does_not_starve_new_messages(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    clock = [1000.0]
    monkeypatch.setattr(mail, "_now", lambda: clock[0])
    monkeypatch.setattr(mail, "_RETRY_BASE_SECONDS", 60)
    conn.messages = {1: b"malformed"}
    assert importer._import_new_messages() == 0
    assert _state(importer)["failures"]["1"]["retry_at"] == 1060
    conn.calls.clear()
    conn.messages[2] = _message((b"new",))
    assert importer._import_new_messages() == 1
    assert not any(call[0] == "fetch" and call[1] == "1" for call in conn.calls)
    clock[0] = 1061
    conn.messages[1] = _message((b"repaired",))
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"new", b"repaired"]


def test_low_free_space_retains_message_until_capacity_recovers(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    conn.messages = {1: _message()}
    real_usage = mail.shutil.disk_usage
    monkeypatch.setattr(mail.shutil, "disk_usage", lambda path: type("Usage", (), {"free": 0})())
    assert importer._import_new_messages() == 0
    assert _contents(importer) == []
    assert "disk" in _state(importer)["failures"]["1"]["reason"].lower()
    monkeypatch.setattr(mail.shutil, "disk_usage", real_usage)
    assert importer._import_new_messages() == 1


def test_literal_limit_is_enforced_before_calling_the_underlying_read(mailbox):
    _, conn, _ = mailbox
    original = conn.read
    mail._limit_imap_reads(conn, mail.time.monotonic() + 10)
    with pytest.raises(mail.imaplib.IMAP4.abort, match="size"):
        conn.read(4002)
    original.assert_not_called()


def test_literal_reads_are_chunked_and_have_a_total_time_budget(mailbox, monkeypatch):
    _, conn, _ = mailbox
    monkeypatch.setattr(mail, "_MAX_MESSAGE_BYTES", 200_000)
    monkeypatch.setattr(mail, "_MAX_FETCH_BYTES_PER_POLL", 400_000)
    original = Mock(side_effect=lambda size: b"x" * size)
    conn.read = original
    mail._limit_imap_reads(conn, mail.time.monotonic() + 10)
    assert conn.read(150_000) == b"x" * 150_000
    assert max(call.args[0] for call in original.call_args_list) <= 64 * 1024
    original.reset_mock()
    mail._limit_imap_reads(conn, mail.time.monotonic() - 1)
    with pytest.raises(TimeoutError):
        conn.read(1)
    original.assert_not_called()


def test_missing_size_metadata_does_not_trigger_an_unbounded_body_fetch(mailbox):
    importer, conn, _ = mailbox
    conn.messages = {1: _message()}
    conn.sizes[1] = "not-a-number"
    assert importer._import_new_messages() == 0
    assert not any("BODY" in str(call) for call in conn.calls)
    assert _state(importer)["pending_uids"] == [1]


def test_uidvalidity_reset_retains_unresolved_old_epoch_diagnostics(mailbox):
    importer, conn, _ = mailbox
    conn.messages = {1: b"malformed"}
    assert importer._import_new_messages() == 0
    conn.generation = b"11"
    conn.messages = {1: _message((b"new-epoch",))}
    assert importer._import_new_messages() == 1
    [history] = importer._state_dir.glob("email-epoch-*.json")
    assert json.loads(history.read_text(encoding="utf-8"))["pending_uids"] == [1]
    assert _state(importer)["pending_uids"] == []


def test_epoch_history_quota_prevents_loss_of_old_diagnostics(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    conn.messages = {1: _message()}
    assert importer._import_new_messages() == 1
    old = mail._state_path(importer._state_dir).read_bytes()
    monkeypatch.setattr(mail, "_MAX_HISTORY_BYTES", 0)
    conn.generation = b"11"
    conn.messages = {1: _message((b"new-epoch",))}
    assert importer._import_new_messages() == 0
    assert mail._state_path(importer._state_dir).read_bytes() == old


def test_corrupt_state_is_not_silently_reset_or_overwritten(mailbox):
    importer, conn, _ = mailbox
    importer._state_dir.mkdir()
    path = mail._state_path(importer._state_dir)
    path.write_bytes(b"broken-json")
    assert importer._import_new_messages() == 0
    assert path.read_bytes() == b"broken-json"
    assert not conn.calls


def test_header_budget_rejects_excessive_headers(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    monkeypatch.setattr(mail, "_MAX_HEADERS", 3)
    conn.messages = {1: _message()}
    assert importer._import_new_messages() == 0
    assert _state(importer)["pending_uids"] == [1]


def test_invalid_base64_remains_pending_instead_of_becoming_partial_content(mailbox):
    importer, conn, _ = mailbox
    conn.messages = {1: _message().replace(b"c3ludGhldGlj", b"!!!!")}
    assert importer._import_new_messages() == 0
    assert _contents(importer) == []
    assert _state(importer)["pending_uids"] == [1]


def test_folder_change_does_not_reuse_the_other_folders_cursor(mailbox):
    importer, conn, _ = mailbox
    conn.messages = {50: _message((b"old-folder",))}
    assert importer._import_new_messages() == 1
    importer._folder = "OTHER"
    conn.messages = {1: _message((b"new-folder",))}
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"new-folder", b"old-folder"]


def test_full_retry_backlog_keeps_new_mail_on_server_without_advancing_cursor(mailbox, monkeypatch):
    importer, conn, _ = mailbox
    monkeypatch.setattr(mail, "_MAX_PENDING_UIDS", 1)
    conn.messages = {1: b"broken", 2: _message((b"later",))}
    assert importer._import_new_messages() == 0
    assert _state(importer)["last_uid"] == 1
    assert importer._import_new_messages() == 0
    assert _state(importer)["last_uid"] == 1
    conn.messages[1] = _message((b"repaired",))
    assert importer._import_new_messages() == 1
    assert importer._import_new_messages() == 1
    assert _contents(importer) == [b"later", b"repaired"]
