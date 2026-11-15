# -*- coding: utf-8 -*-
"""A scripted stand-in for the Swisseldex FTPES server (no network, no TLS).

It implements the part of the ftplib.FTP_TLS interface that
shareomat.core.collector.swisseldex_ftpes uses. Deliberately absent: delete,
rename, upload - a call to any of them fails the test, so the receiver
provably never changes the mailbox.
"""
from __future__ import annotations

import ftplib
import ssl
from dataclasses import dataclass, field

from shareomat.core.collector.swisseldex_ftpes import SwisseldexFtpesSession


@dataclass
class FakeMailbox:
    """Server-side state shared by all sessions of one test."""

    files: dict[str, bytes] = field(default_factory=dict)
    modified: dict[str, str] = field(default_factory=dict)
    mlsd_supported: bool = True
    connect_error: Exception | None = None
    auth_error: Exception | None = None
    login_error: Exception | None = None
    listing_error: Exception | None = None
    # name -> (bytes delivered before the failure, exception)
    broken_transfer: dict[str, tuple[int, Exception]] = field(default_factory=dict)
    # name -> exception raised AFTER all bytes were delivered (unclean TLS close)
    error_after_transfer: dict[str, Exception] = field(default_factory=dict)
    # name -> number of bytes the server silently withholds
    short_transfer: dict[str, int] = field(default_factory=dict)
    quit_error: Exception | None = None
    sessions: int = 0
    retr_calls: list[str] = field(default_factory=list)
    logins: list[tuple[str, str]] = field(default_factory=list)
    on_chunk: object = None

    def put(self, name: str, content: bytes, modified: str = "20261207053000") -> None:
        self.files[name] = content
        self.modified[name] = modified


class FakeFtp:
    def __init__(self, mailbox: FakeMailbox) -> None:
        self.box = mailbox
        self.pasv = None
        self.protected = False
        self.closed = False

    def set_pasv(self, value: bool) -> None:
        self.pasv = value

    def connect(self, host, port, timeout=None):
        if self.box.connect_error:
            raise self.box.connect_error
        self.box.sessions += 1
        return "220 Datahub"

    def auth(self):
        if self.box.auth_error:
            raise self.box.auth_error
        return "234 AUTH TLS ok"

    def login(self, user, passwd):
        self.box.logins.append((user, passwd))
        if self.box.login_error:
            raise self.box.login_error
        return "230 ok"

    def prot_p(self):
        self.protected = True
        return "200 PROT P"

    def mlsd(self, path="", facts=()):
        if self.box.listing_error:
            raise self.box.listing_error
        if not self.box.mlsd_supported:
            raise ftplib.error_perm("502 Command not implemented")
        for name, content in list(self.box.files.items()):
            yield name, {"type": "file", "size": str(len(content)), "modify": self.box.modified.get(name, "")}

    def nlst(self, *args):
        if self.box.listing_error:
            raise self.box.listing_error
        if not self.box.files:
            raise ftplib.error_perm("550 No files found")
        return list(self.box.files)

    def sendcmd(self, cmd):
        if cmd.startswith("MDTM "):
            name = cmd[5:]
            return f"213 {self.box.modified[name]}"
        raise ftplib.error_perm("502 Command not implemented")

    def voidcmd(self, cmd):
        return "200 ok"

    def size(self, name):
        return len(self.box.files[name])

    def retrbinary(self, cmd, callback, blocksize=8192, rest=None):
        assert self.protected, "RETR without PROT P is refused by the Datahub (521)"
        name = cmd[5:]
        self.box.retr_calls.append(name)
        content = self.box.files[name]
        if name in self.box.short_transfer:
            content = content[: len(content) - self.box.short_transfer[name]]
        limit, error = self.box.broken_transfer.get(name, (None, None))
        data = content if limit is None else content[:limit]
        for start in range(0, len(data), blocksize):
            if callable(self.box.on_chunk):
                self.box.on_chunk(name)
            callback(data[start:start + blocksize])
        if error is not None:
            raise error
        if name in self.box.error_after_transfer:
            raise self.box.error_after_transfer[name]
        return "226 Transfer complete"

    def quit(self):
        if self.box.quit_error:
            raise self.box.quit_error
        return "221 bye"

    def close(self):
        self.closed = True


def session_factory(mailbox: FakeMailbox, username: str = "cem-user", password: str = "pw-geheim-123"):
    """A SwisseldexReceiver session_factory speaking to `mailbox`."""
    def _make() -> SwisseldexFtpesSession:
        return SwisseldexFtpesSession(
            "datahub.example.invalid", 21, username, password,
            timeout=5, ftp_factory=lambda context, timeout: FakeFtp(mailbox),
        )
    return _make


TLS_EOF = ssl.SSLEOFError(8, "EOF occurred in violation of protocol")
