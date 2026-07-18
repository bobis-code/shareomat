# -*- coding: utf-8 -*-
"""Tests for the IMAP email importer (attachment extraction, sender filtering, UID state)."""
from __future__ import annotations

from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from unittest.mock import MagicMock, patch

from shareomat.core.collector.leg_email_importer import (
    EmailImporterThread,
    _extract_attachments,
    _load_last_uid,
    _save_attachment,
    _save_last_uid,
    _sender_allowed,
)


def _make_message(from_addr: str, attachments: list[tuple[str, bytes]]) -> MIMEMultipart:
    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["Subject"] = "Meter data"
    msg.attach(MIMEText("see attached", "plain"))
    for filename, content in attachments:
        part = MIMEApplication(content)
        part.add_header("Content-Disposition", "attachment", filename=filename)
        msg.attach(part)
    return msg


def test_sender_allowed_empty_list_allows_any():
    msg = _make_message("Grid Operator <grid@operator.ch>", [])
    assert _sender_allowed(msg, []) is True


def test_sender_allowed_matches_case_insensitive():
    msg = _make_message("Grid Operator <Grid@Operator.CH>", [])
    assert _sender_allowed(msg, ["grid@operator.ch"]) is True


def test_sender_allowed_rejects_unknown():
    msg = _make_message("Someone Else <spam@example.com>", [])
    assert _sender_allowed(msg, ["grid@operator.ch"]) is False


def test_extract_attachments_filters_by_extension():
    msg = _make_message(
        "grid@operator.ch",
        [("readings.csv", b"a,b,c"), ("flyer.pdf", b"%PDF-1.4")],
    )
    attachments = _extract_attachments(msg)
    assert [name for name, _ in attachments] == ["readings.csv"]
    assert attachments[0][1] == b"a,b,c"


def test_save_attachment_avoids_overwrite(tmp_path):
    first = _save_attachment(tmp_path, uid=1, filename="readings.csv", content=b"first")
    second = _save_attachment(tmp_path, uid=2, filename="readings.csv", content=b"second")

    assert first.name == "readings.csv"
    assert second.name == "2_readings.csv"
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"


def test_uid_state_roundtrip(tmp_path):
    assert _load_last_uid(tmp_path) == 0
    _save_last_uid(tmp_path, 42)
    assert _load_last_uid(tmp_path) == 42


def test_import_new_messages_saves_matching_attachment(tmp_path):
    inbox = tmp_path / "inbox"
    state = tmp_path / "state"
    msg = _make_message("grid@operator.ch", [("readings.csv", b"a,b,c")])

    mock_conn = MagicMock()
    mock_conn.login.return_value = ("OK", [b""])
    mock_conn.select.return_value = ("OK", [b""])
    mock_conn.uid.side_effect = [
        ("OK", [b"5"]),
        ("OK", [(b"5 (BODY[] {123}", msg.as_bytes())]),
    ]

    with patch("imaplib.IMAP4_SSL", return_value=mock_conn):
        importer = EmailImporterThread(
            imap_host="imap.gmail.com",
            imap_port=993,
            username="energieverteiler1@gmail.com",
            password="app-password",
            folder="INBOX",
            allowed_senders=["grid@operator.ch"],
            inbox_path=inbox,
            state_dir=state,
        )
        count = importer._import_new_messages()

    assert count == 1
    assert (inbox / "readings.csv").read_bytes() == b"a,b,c"
    assert _load_last_uid(state) == 5
    mock_conn.logout.assert_called_once()
