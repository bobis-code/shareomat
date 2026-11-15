# -*- coding: utf-8 -*-
"""FTPES client for the Swisseldex mailbox - simulated server, no network."""
from __future__ import annotations

import ftplib
import io
import ssl
import threading
from types import SimpleNamespace

import pytest

from shareomat.core.collector import swisseldex_ftpes as ftpes
from shareomat.core.collector.swisseldex_ftpes import (
    PHASE_CONNECT,
    PHASE_DOWNLOAD,
    PHASE_LISTING,
    PHASE_LOGIN,
    PHASE_TLS,
    DownloadAborted,
    SwisseldexFtpError,
    SwisseldexFtpesSession,
)
from tests.swisseldex_fake import TLS_EOF, FakeFtp, FakeMailbox

PASSWORD = "pw-geheim-123"


def _session(box: FakeMailbox, holder: list | None = None) -> SwisseldexFtpesSession:
    def factory(context, timeout):
        ftp = FakeFtp(box)
        if holder is not None:
            holder.append(ftp)
        return ftp
    return SwisseldexFtpesSession("datahub.example.invalid", 21, "cem-user", PASSWORD, timeout=5, ftp_factory=factory)


def test_login_uses_passive_mode_and_protected_data_channel():
    box = FakeMailbox()
    holder: list[FakeFtp] = []
    with _session(box, holder) as session:
        assert session.list_files() == []
    ftp = holder[0]
    assert ftp.pasv is True and ftp.protected is True and ftp.closed is True
    assert box.logins == [("cem-user", PASSWORD)]


def test_empty_mailbox_is_a_successful_listing():
    with _session(FakeMailbox()) as session:
        assert session.list_files() == []


def test_listing_reports_size_and_timestamp():
    box = FakeMailbox()
    box.put("E66_1.xml", b"<a/>", "20261207050000")
    with _session(box) as session:
        files = session.list_files()
    assert [(f.name, f.size, f.modified) for f in files] == [("E66_1.xml", 4, "20261207050000")]


def test_listing_falls_back_to_nlst_size_and_mdtm():
    box = FakeMailbox(mlsd_supported=False)
    box.put("E31_1.xml.gz", b"12345", "20261207060000")
    with _session(box) as session:
        files = session.list_files()
    assert [(f.name, f.size, f.modified) for f in files] == [("E31_1.xml.gz", 5, "20261207060000")]


def test_nlst_on_empty_mailbox_is_not_an_error():
    with _session(FakeMailbox(mlsd_supported=False)) as session:
        assert session.list_files() == []


def test_unreachable_server_is_a_connect_error():
    box = FakeMailbox(connect_error=OSError("Name or service not known"))
    with pytest.raises(SwisseldexFtpError) as err:
        _session(box).open()
    assert err.value.phase == PHASE_CONNECT
    assert err.value.retryable is True
    assert "nicht erreichbar" in err.value.user_message


def test_certificate_error_is_not_retried():
    box = FakeMailbox(auth_error=ssl.SSLCertVerificationError(1, "certificate verify failed"))
    with pytest.raises(SwisseldexFtpError) as err:
        _session(box).open()
    assert err.value.phase == PHASE_TLS and err.value.retryable is False


def test_wrong_password_is_a_login_error_without_the_password_in_it():
    box = FakeMailbox(login_error=ftplib.error_perm(f"530 Login incorrect for {PASSWORD}"))
    with pytest.raises(SwisseldexFtpError) as err:
        _session(box).open()
    assert err.value.phase == PHASE_LOGIN
    assert err.value.retryable is False
    assert "Anmeldung fehlgeschlagen" in err.value.user_message
    assert PASSWORD not in err.value.detail and PASSWORD not in str(err.value)


def test_other_login_refusal_stays_retryable():
    box = FakeMailbox(login_error=ftplib.error_temp("421 Too many connections"))
    with pytest.raises(SwisseldexFtpError) as err:
        _session(box).open()
    assert err.value.phase == PHASE_LOGIN and err.value.retryable is True


def test_listing_timeout_is_a_listing_error():
    box = FakeMailbox(listing_error=TimeoutError("timed out"))
    with _session(box) as session:
        with pytest.raises(SwisseldexFtpError) as err:
            session.list_files()
    assert err.value.phase == PHASE_LISTING


def test_complete_download_has_hash_and_size():
    box = FakeMailbox()
    box.put("a.xml", b"x" * 200_000)
    target = io.BytesIO()
    with _session(box) as session:
        result = session.download("a.xml", target, expected_size=200_000)
    assert result.complete and result.bytes_received == 200_000 and target.getvalue() == b"x" * 200_000
    assert len(result.sha256) == 64


def test_interrupted_download_is_an_error():
    box = FakeMailbox()
    box.put("a.xml", b"x" * 1000)
    box.broken_transfer["a.xml"] = (300, ConnectionResetError("reset by peer"))
    with _session(box) as session:
        with pytest.raises(SwisseldexFtpError) as err:
            session.download("a.xml", io.BytesIO(), expected_size=1000)
    assert err.value.phase == PHASE_DOWNLOAD and err.value.retryable is True


def test_tls_close_error_after_complete_download_is_only_a_note():
    """The GnuTLS -110 case: all bytes arrived, only the TLS shutdown was not clean."""
    box = FakeMailbox()
    box.put("a.xml", b"<ok/>")
    box.error_after_transfer["a.xml"] = TLS_EOF
    with _session(box) as session:
        result = session.download("a.xml", io.BytesIO(), expected_size=5)
    assert result.complete and "nicht sauber beendet" in result.note


def test_close_error_without_known_size_stays_an_error():
    box = FakeMailbox()
    box.put("a.xml", b"<ok/>")
    box.error_after_transfer["a.xml"] = TLS_EOF
    with _session(box) as session:
        with pytest.raises(SwisseldexFtpError):
            session.download("a.xml", io.BytesIO(), expected_size=None)


def test_silently_short_download_is_incomplete():
    box = FakeMailbox()
    box.put("a.xml", b"0123456789")
    box.short_transfer["a.xml"] = 3
    with _session(box) as session:
        with pytest.raises(SwisseldexFtpError) as err:
            session.download("a.xml", io.BytesIO(), expected_size=10)
    assert "unvollständig" in err.value.user_message


def test_file_above_limit_is_refused_and_not_retried(monkeypatch):
    monkeypatch.setattr(ftpes, "MAX_FILE_BYTES", 100)
    box = FakeMailbox()
    box.put("big.xml", b"y" * 500)
    with _session(box) as session:
        with pytest.raises(SwisseldexFtpError) as err:
            session.download("big.xml", io.BytesIO(), expected_size=None)
    assert err.value.retryable is False


def test_stop_aborts_a_running_download():
    box = FakeMailbox()
    box.put("a.xml", b"z" * 300_000)
    stop = threading.Event()
    box.on_chunk = lambda name: stop.set()
    with _session(box) as session:
        with pytest.raises(DownloadAborted):
            session.download("a.xml", io.BytesIO(), expected_size=300_000, should_stop=stop)


def test_failing_quit_is_recorded_not_raised():
    box = FakeMailbox(quit_error=TLS_EOF)
    session = _session(box)
    with session:
        session.list_files()
    assert "ohne Folgen" in session.close_note


def test_tls_context_verifies_certificate_and_host_name():
    context = ftpes._tls_context()
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2


def test_data_channel_resumes_the_control_channel_tls_session(monkeypatch):
    calls = {}

    class Context:
        def wrap_socket(self, sock, server_hostname=None, session=None):
            calls.update(sock=sock, host=server_hostname, session=session)
            return "wrapped"

    monkeypatch.setattr(ftplib.FTP, "ntransfercmd", lambda self, cmd, rest=None: ("raw-data-socket", 42))
    client = ftpes._SessionReusingFTPTLS(context=Context())
    client.host = "datahub.example.invalid"
    client.sock = SimpleNamespace(session="control-session")
    client._prot_p = True
    assert client.ntransfercmd("RETR a.xml") == ("wrapped", 42)
    assert calls == {"sock": "raw-data-socket", "host": "datahub.example.invalid", "session": "control-session"}


def test_client_has_no_delete_or_upload():
    for name in ("delete", "rename", "upload", "store", "remove"):
        assert not hasattr(SwisseldexFtpesSession, name)
