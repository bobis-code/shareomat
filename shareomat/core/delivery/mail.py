# -*- coding: utf-8 -*-
"""
File: shareomat/core/delivery/mail.py

Purpose:
    Build and send one e-mail (plain text + PDF attachments) over SMTP,
    with the outgoing mailbox from the add-on options (SmtpConfig).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    - Always encrypted: implicit TLS (ssl, port 465) or STARTTLS (port 587),
      both with Python's default certificate and host name verification.
      There is no unencrypted mode and no "accept any certificate" switch.
    - Exactly one recipient per e-mail, no Cc/Bcc: a document is meant for
      its participant only.
    - Header injection: addresses are checked strictly, subject and names
      must be single-line, and the e-mail is built with email.policy.SMTP,
      which refuses line breaks in header values a second time.
    - The password never appears in a message, a log line or a repr.
    - MailError.uncertain: the connection broke after the message was
      handed to the server, so it may have been sent. The operator checks
      the "Gesendet" folder before sending again.
"""

from __future__ import annotations

import logging
import re
import smtplib
import socket
import ssl
from dataclasses import dataclass, field
from email.message import EmailMessage
from email.policy import SMTP as SMTP_POLICY
from email.utils import formataddr, formatdate, make_msgid

from shareomat.config import SMTP_SECURITY_SSL, SMTP_SECURITY_STARTTLS, SmtpConfig, smtp_problems

logger = logging.getLogger(__name__)

MAX_ATTACHMENT_BYTES = 15 * 1024 * 1024     # Gmail accepts 25 MB including encoding overhead

# Deliberately stricter than RFC 5321: ASCII only, no quotes, no spaces, no brackets.
_ADDRESS = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
                      r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$")
_LINE_BREAK = re.compile(r"[\r\n\x00\x0b\x0c\x85  ]")
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}\.pdf$")


class MailError(RuntimeError):
    """Sending failed; the message is meant for the operator (German).

    uncertain=True: the server may have accepted the message anyway.
    """

    def __init__(self, message: str, *, uncertain: bool = False) -> None:
        super().__init__(message)
        self.uncertain = uncertain


@dataclass(frozen=True)
class Attachment:
    filename: str
    data: bytes
    maintype: str = "application"
    subtype: str = "pdf"


@dataclass(frozen=True)
class OutgoingMail:
    to: str
    subject: str
    body: str
    attachments: tuple[Attachment, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class SentMail:
    message_id: str
    to: str
    size_bytes: int


# ── Private helpers ─────────────────────────────────────────────────────────


def _connect(smtp: SmtpConfig, context: ssl.SSLContext) -> smtplib.SMTP:
    if smtp.security == SMTP_SECURITY_SSL:
        return smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=smtp.timeout_seconds, context=context)
    if smtp.security == SMTP_SECURITY_STARTTLS:
        connection = smtplib.SMTP(smtp.host, smtp.port, timeout=smtp.timeout_seconds)
        try:
            connection.ehlo()
            if not connection.has_extn("starttls"):
                raise MailError("Der Mailserver bietet kein STARTTLS an — unverschlüsselt wird nicht gesendet.")
            connection.starttls(context=context)
            connection.ehlo()
        except BaseException:
            connection.close()
            raise
        return connection
    raise MailError("Verschlüsselung muss «ssl» oder «starttls» sein.")


def _before_send_error(exc: BaseException) -> MailError:
    """An error before the message was handed over: certainly not sent."""
    if isinstance(exc, MailError):
        return exc
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailError("Anmeldung beim Mailserver abgelehnt. Bei Gmail ein App-Passwort verwenden (nicht das "
                         "normale Passwort) und Benutzername prüfen. Nicht gesendet.")
    if isinstance(exc, ssl.SSLCertVerificationError):
        return MailError("Das Zertifikat des Mailservers ist ungültig oder gehört nicht zu diesem Servernamen. "
                         "Nicht gesendet.")
    if isinstance(exc, ssl.SSLError):
        return MailError(f"Verschlüsselte Verbindung zum Mailserver fehlgeschlagen ({exc.reason or exc}). "
                         "Port und Verschlüsselung (465 = ssl, 587 = starttls) prüfen. Nicht gesendet.")
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return MailError("Der Mailserver antwortet nicht (Zeitüberschreitung). Nicht gesendet.")
    if isinstance(exc, smtplib.SMTPException):
        return MailError(f"Der Mailserver hat abgelehnt: {_server_text(exc)}. Nicht gesendet.")
    if isinstance(exc, OSError):
        return MailError(f"Mailserver nicht erreichbar ({exc.strerror or type(exc).__name__}). Nicht gesendet.")
    return MailError(f"Unerwarteter Fehler beim Verbinden: {type(exc).__name__}. Nicht gesendet.")


def _server_text(exc: smtplib.SMTPException) -> str:
    text = getattr(exc, "smtp_error", b"") or str(exc).encode()
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    code = getattr(exc, "smtp_code", None)
    text = " ".join(text.split())[:200]
    return f"{code} {text}" if code else text


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[1]


# ── Public functions ────────────────────────────────────────────────────────


def valid_address(address: str) -> bool:
    """A plain ASCII address user@domain.tld — nothing a header could be split or redirected with."""
    return bool(address) and len(address) <= 254 and _ADDRESS.fullmatch(address) is not None


def build_message(smtp: SmtpConfig, mail: OutgoingMail) -> EmailMessage:
    """The e-mail exactly as it will be sent; raises MailError for anything unsafe."""
    if not valid_address(smtp.from_address):
        raise MailError("Die Absenderadresse in der Add-on-Konfiguration ist ungültig.")
    if not valid_address(mail.to):
        raise MailError(f"Ungültige Empfängeradresse «{mail.to[:80]}» — nicht gesendet.")
    if not mail.subject.strip() or _LINE_BREAK.search(mail.subject) or _LINE_BREAK.search(smtp.from_name):
        raise MailError("Betreff oder Absendername ist leer oder mehrzeilig — nicht gesendet.")
    message = EmailMessage(policy=SMTP_POLICY)
    message["From"] = formataddr((smtp.from_name, smtp.from_address)) if smtp.from_name else smtp.from_address
    message["To"] = mail.to
    message["Subject"] = mail.subject.strip()
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=_domain(smtp.from_address))
    message.set_content(mail.body.replace("\r\n", "\n").replace("\r", "\n"), charset="utf-8")
    total = 0
    for attachment in mail.attachments:
        if not _SAFE_FILENAME.fullmatch(attachment.filename):
            raise MailError(f"Ungültiger Dateiname für den Anhang: {attachment.filename[:60]}")
        total += len(attachment.data)
        if total > MAX_ATTACHMENT_BYTES:
            raise MailError("Anhänge sind zu gross für eine E-Mail — nicht gesendet.")
        message.add_attachment(attachment.data, maintype=attachment.maintype, subtype=attachment.subtype,
                               filename=attachment.filename)
    return message


def send_mail(smtp: SmtpConfig, mail: OutgoingMail) -> SentMail:
    """Send one e-mail; raises MailError (uncertain=True when it may have gone out anyway)."""
    problems = smtp_problems(smtp)
    if problems:
        raise MailError(problems[0])
    message = build_message(smtp, mail)
    raw_size = len(message.as_bytes())
    context = ssl.create_default_context()
    try:
        connection = _connect(smtp, context)
    except BaseException as exc:
        if not isinstance(exc, Exception):
            raise
        raise _before_send_error(exc) from None
    try:
        try:
            connection.login(smtp.username, smtp.password)
        except Exception as exc:
            raise _before_send_error(exc) from None
        try:
            refused = connection.send_message(message, from_addr=smtp.from_address, to_addrs=[mail.to])
        except smtplib.SMTPRecipientsRefused as exc:
            details = "; ".join(f"{code} {text.decode('utf-8', 'replace') if isinstance(text, bytes) else text}"
                                for code, text in exc.recipients.values())
            raise MailError(f"Der Mailserver lehnt die Empfängeradresse ab ({details[:200]}). Nicht gesendet.") from None
        except smtplib.SMTPSenderRefused as exc:
            raise MailError(f"Der Mailserver lehnt die Absenderadresse ab ({_server_text(exc)}). "
                            "Bei Gmail muss sie die Adresse des Kontos sein. Nicht gesendet.") from None
        except smtplib.SMTPDataError as exc:
            raise MailError(f"Der Mailserver hat die Nachricht abgelehnt ({_server_text(exc)}). Nicht gesendet.") from None
        except Exception as exc:
            # The connection broke while or after the message was handed over.
            raise MailError(
                f"Die Verbindung brach beim Senden ab ({type(exc).__name__}). Ob die E-Mail angekommen ist, ist "
                "unklar: bitte im Postfach unter «Gesendet» nachsehen, bevor Sie erneut senden.",
                uncertain=True) from None
        if refused:
            raise MailError("Der Mailserver hat die Empfängeradresse abgelehnt. Nicht gesendet.")
    finally:
        try:
            connection.quit()
        except Exception:
            connection.close()
    logger.info("E-mail sent (%d bytes, %s)", raw_size, message["Message-ID"])
    return SentMail(message_id=str(message["Message-ID"]), to=mail.to, size_bytes=raw_size)
