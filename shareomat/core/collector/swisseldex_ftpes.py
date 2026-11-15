# -*- coding: utf-8 -*-
"""
File: shareomat/core/collector/swisseldex_ftpes.py

Purpose:
    FTPES client for the Swisseldex Datahub mailbox ("Server beim Datahub"):
    connect, log in, list the mailbox and download single files. Nothing else -
    no archive, no state, no SDAT knowledge and no LEG/billing logic.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Server behaviour this client relies on, from the Swisseldex user manual
    (Stand 28.09.2026, chapter 3.4 and 4):

    * Explicit TLS on port 21, passive mode. Login sequence
      AUTH TLS, USER, PASS, PBSZ, PROT P; RETR without PROT P is refused (521).
    * One flat directory, received files are listed in "/".
    * No REST: an interrupted download cannot be resumed, it starts over.
    * SIZE, MDTM and MLSD are supported, FEAT is not.
    * The server disconnects after 10 s without activity. A session here is
      always connect, list, download, quit - nothing slow in between.
    * Repeated logins within 60 s are refused. This client does not enforce
      that, the caller does (shareomat.core.collector.swisseldex_receiver).

    Nothing in this module deletes, renames or uploads files on the server.

    Needs verification (first real test): whether the Datahub requires the
    data channel to reuse the control channel's TLS session. Python's ftplib
    does not do that by default; _SessionReusingFTPTLS does it, which is also
    harmless for servers that do not require it.

    Each failure carries the phase it happened in (connect, tls, login,
    listing, download), so the caller can tell "Swisseldex not reachable" from
    "wrong password". A failure while closing an otherwise complete session is
    not an error at all, only a note.
"""

from __future__ import annotations

import ftplib
import hashlib
import logging
import ssl
import threading
from dataclasses import dataclass
from typing import BinaryIO, Callable

logger = logging.getLogger(__name__)

# Upper limit the Datahub itself enforces for a single file (manual 3.4 / 4.2).
MAX_FILE_BYTES = 50 * 1024 * 1024

PHASE_CONNECT = "connect"
PHASE_TLS = "tls"
PHASE_LOGIN = "login"
PHASE_LISTING = "listing"
PHASE_DOWNLOAD = "download"

_CHUNK_BYTES = 64 * 1024

# What the operator sees per phase; technical details go to the diagnostic detail.
_USER_MESSAGES = {
    PHASE_CONNECT: "Swisseldex ist momentan nicht erreichbar",
    PHASE_TLS: "Verschlüsselte Verbindung zu Swisseldex fehlgeschlagen",
    PHASE_LOGIN: "Anmeldung fehlgeschlagen",
    PHASE_LISTING: "Verzeichnis konnte nicht gelesen werden",
    PHASE_DOWNLOAD: "Download fehlgeschlagen",
}


class SwisseldexFtpError(Exception):
    """A failure in one phase of an FTPES session.

    `user_message` is short German text for the operator, `detail` is the
    technical reason for the diagnostic log. Neither ever contains the password.
    `retryable` is False where repeating would not help or could lock the
    account (wrong credentials, certificate problems).
    """

    def __init__(self, phase: str, detail: str, *, user_message: str = "", retryable: bool = True) -> None:
        super().__init__(f"{phase}: {detail}")
        self.phase = phase
        self.detail = detail
        self.user_message = user_message or _USER_MESSAGES.get(phase, "Fehler")
        self.retryable = retryable


class DownloadAborted(SwisseldexFtpError):
    """The caller stopped a running download (shutdown). The file is incomplete."""

    def __init__(self) -> None:
        super().__init__(PHASE_DOWNLOAD, "aborted by shutdown", user_message="Download wegen Neustart abgebrochen")


@dataclass(frozen=True)
class RemoteFile:
    """One entry of the mailbox listing. size/modified are None when the server did not say."""

    name: str
    size: int | None
    modified: str | None   # server timestamp as sent (MLSD "modify" / MDTM), YYYYMMDDHHMMSS[.sss]


@dataclass(frozen=True)
class DownloadResult:
    """A download that ended. `complete` is True only if the byte count is proven."""

    bytes_received: int
    sha256: str
    complete: bool
    note: str = ""


# ── Private helpers ───────────────────────────────────────────────────────────


class _SessionReusingFTPTLS(ftplib.FTP_TLS):
    """FTP_TLS whose data connections resume the control connection's TLS session.

    Many FTPES servers refuse a data connection that starts a fresh TLS
    session; FileZilla resumes the session by default, ftplib does not.
    """

    def ntransfercmd(self, cmd, rest=None):  # noqa: ANN001 - signature of ftplib
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            session = getattr(self.sock, "session", None)
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=session)
        return conn, size


def _tls_context() -> ssl.SSLContext:
    """Certificate and host name are always verified; TLS 1.2 is the minimum."""
    context = ssl.create_default_context()
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _clean(text: object, secret: str) -> str:
    """Technical detail for the log: one line, bounded, the password never in it."""
    message = " ".join(str(text).split())
    if secret:
        message = message.replace(secret, "***")
    return message[:300]


def _parse_size(value: object) -> int | None:
    try:
        size = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return size if size >= 0 else None


def _is_auth_failure(exc: ftplib.Error) -> bool:
    """530 = not logged in (wrong user or password). 421 = service not available / too many."""
    return str(exc).startswith("530")


# ── Public interface ──────────────────────────────────────────────────────────


class SwisseldexFtpesSession:
    """One FTPES session to the Datahub. Use as a context manager.

        with SwisseldexFtpesSession(host, port, user, password, timeout=30) as session:
            files = session.list_files()
            result = session.download("x.xml", fileobj, expected_size=..., should_stop=...)

    `ftp_factory` exists for tests: it returns an object with the ftplib.FTP_TLS
    interface. Production uses _SessionReusingFTPTLS with a verifying context.
    """

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        timeout: float = 30.0,
        ftp_factory: Callable[[ssl.SSLContext, float], ftplib.FTP_TLS] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._timeout = timeout
        self._factory = ftp_factory or (lambda context, t: _SessionReusingFTPTLS(context=context, timeout=t))
        self._ftp: ftplib.FTP_TLS | None = None
        self.close_note = ""

    def __enter__(self) -> "SwisseldexFtpesSession":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def open(self) -> None:
        """Connect, switch to TLS, log in and protect the data channel."""
        ftp = self._factory(_tls_context(), self._timeout)
        ftp.set_pasv(True)
        self._ftp = ftp
        try:
            ftp.connect(self._host, self._port, timeout=self._timeout)
        except OSError as exc:  # DNS failure, refused, timeout, network down
            self._drop()
            raise SwisseldexFtpError(PHASE_CONNECT, _clean(exc, self._password)) from exc
        except ftplib.Error as exc:
            self._drop()
            raise SwisseldexFtpError(PHASE_CONNECT, _clean(exc, self._password)) from exc
        try:
            ftp.auth()
        except ssl.SSLCertVerificationError as exc:
            self._drop()
            raise SwisseldexFtpError(
                PHASE_TLS, _clean(exc, self._password),
                user_message="Zertifikat von Swisseldex konnte nicht geprüft werden", retryable=False,
            ) from exc
        except (ssl.SSLError, OSError, ftplib.Error) as exc:
            self._drop()
            raise SwisseldexFtpError(PHASE_TLS, _clean(exc, self._password)) from exc
        try:
            ftp.login(self._username, self._password)
        except ftplib.Error as exc:
            self._drop()
            if _is_auth_failure(exc):
                raise SwisseldexFtpError(
                    PHASE_LOGIN, _clean(exc, self._password),
                    user_message="Anmeldung fehlgeschlagen – Benutzername oder Passwort prüfen", retryable=False,
                ) from exc
            raise SwisseldexFtpError(PHASE_LOGIN, _clean(exc, self._password)) from exc
        except (ssl.SSLError, OSError) as exc:
            self._drop()
            raise SwisseldexFtpError(PHASE_LOGIN, _clean(exc, self._password)) from exc
        try:
            ftp.prot_p()
        except (ftplib.Error, ssl.SSLError, OSError) as exc:
            self._drop()
            raise SwisseldexFtpError(PHASE_TLS, f"PROT P: {_clean(exc, self._password)}") from exc
        logger.debug("Swisseldex FTPES session open on %s:%s", self._host, self._port)

    def list_files(self) -> list[RemoteFile]:
        """All plain files in the mailbox root. MLSD first, NLST + SIZE/MDTM as fallback."""
        ftp = self._require()
        try:
            entries = list(ftp.mlsd("", facts=["type", "size", "modify"]))
        except ftplib.error_perm as exc:
            logger.debug("MLSD refused (%s), falling back to NLST", _clean(exc, self._password))
            return self._list_with_nlst()
        except (ftplib.Error, ssl.SSLError, OSError, EOFError) as exc:
            raise SwisseldexFtpError(PHASE_LISTING, _clean(exc, self._password)) from exc
        files: list[RemoteFile] = []
        for name, facts in entries:
            if facts.get("type", "file").lower() != "file":
                continue
            files.append(RemoteFile(name=name, size=_parse_size(facts.get("size")), modified=facts.get("modify")))
        return files

    def download(
        self,
        name: str,
        target: BinaryIO,
        *,
        expected_size: int | None,
        should_stop: Callable[[], bool] | threading.Event | None = None,
    ) -> DownloadResult:
        """RETR one file into `target`, counting bytes and hashing on the way.

        Raises SwisseldexFtpError when the transfer failed or was incomplete.
        A TLS/close error AFTER all expected bytes arrived only adds a note:
        the content is proven complete by its size (the GnuTLS -110 case).
        Without a known size such an error stays an error (conservative).
        """
        ftp = self._require()
        stop = should_stop.is_set if isinstance(should_stop, threading.Event) else (should_stop or (lambda: False))
        digest = hashlib.sha256()
        received = 0

        def _write(chunk: bytes) -> None:
            nonlocal received
            if stop():
                raise DownloadAborted()
            received += len(chunk)
            if received > MAX_FILE_BYTES:
                raise SwisseldexFtpError(
                    PHASE_DOWNLOAD, f"larger than {MAX_FILE_BYTES} bytes",
                    user_message="Datei grösser als 50 MB", retryable=False,
                )
            digest.update(chunk)
            target.write(chunk)

        note = ""
        try:
            ftp.retrbinary(f"RETR {name}", _write, blocksize=_CHUNK_BYTES)
        except SwisseldexFtpError:
            raise
        except (ssl.SSLError, OSError, EOFError, ftplib.Error) as exc:
            if expected_size is not None and received == expected_size and not isinstance(exc, ftplib.error_perm):
                note = f"Verbindung nach vollständigem Download nicht sauber beendet ({_clean(exc, self._password)})"
            else:
                raise SwisseldexFtpError(PHASE_DOWNLOAD, _clean(exc, self._password)) from exc

        if expected_size is not None and received != expected_size:
            raise SwisseldexFtpError(
                PHASE_DOWNLOAD, f"{received} of {expected_size} bytes received",
                user_message="Download unvollständig",
            )
        return DownloadResult(
            bytes_received=received, sha256=digest.hexdigest(),
            complete=expected_size is not None, note=note,
        )

    def size_of(self, name: str) -> int | None:
        """SIZE of one file, None if the server does not answer it."""
        ftp = self._require()
        try:
            ftp.voidcmd("TYPE I")
            return _parse_size(ftp.size(name))
        except (ftplib.Error, ssl.SSLError, OSError):
            return None

    def close(self) -> None:
        """QUIT politely; a failing QUIT/TLS shutdown is only recorded in close_note."""
        ftp = self._ftp
        if ftp is None:
            return
        try:
            ftp.quit()
        except (ftplib.Error, ssl.SSLError, OSError, EOFError) as exc:
            self.close_note = f"Sitzung nicht sauber beendet (ohne Folgen): {_clean(exc, self._password)}"
        finally:
            self._drop()

    # ── internals ──

    def _require(self) -> ftplib.FTP_TLS:
        if self._ftp is None:
            raise SwisseldexFtpError(PHASE_CONNECT, "session is not open")
        return self._ftp

    def _drop(self) -> None:
        ftp, self._ftp = self._ftp, None
        if ftp is not None:
            try:
                ftp.close()
            except Exception:  # closing a broken socket must not mask the real error
                pass

    def _list_with_nlst(self) -> list[RemoteFile]:
        ftp = self._require()
        try:
            names = ftp.nlst()
        except ftplib.error_perm as exc:
            # "550 No files found" is how some servers answer NLST on an empty directory.
            if str(exc).startswith("550"):
                return []
            raise SwisseldexFtpError(PHASE_LISTING, _clean(exc, self._password)) from exc
        except (ftplib.Error, ssl.SSLError, OSError, EOFError) as exc:
            raise SwisseldexFtpError(PHASE_LISTING, _clean(exc, self._password)) from exc
        files: list[RemoteFile] = []
        for name in names:
            name = name.rsplit("/", 1)[-1]
            if name in ("", ".", ".."):
                continue
            modified = None
            try:
                response = ftp.sendcmd(f"MDTM {name}")
                if response.startswith("213"):
                    modified = response[3:].strip()
            except (ftplib.Error, ssl.SSLError, OSError):
                modified = None
            files.append(RemoteFile(name=name, size=self.size_of(name), modified=modified))
        return files
