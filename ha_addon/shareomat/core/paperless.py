# -*- coding: utf-8 -*-
"""
File: shareomat/core/paperless.py

Purpose:
    Links to evidence documents kept in Paperless-ngx: validation of the
    configured base URL and of document numbers / archive serial numbers
    (ASN), and the link itself.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Shareomat never contacts Paperless: no API token, no request, no
    download. Only the operator's browser follows the link. The base URL
    comes from the add-on options only (never from a form or the
    database), must be plain http(s) with a host and carries no
    credentials, query or fragment. Only integer numbers are stored per
    evidence, never a free link.
"""

from __future__ import annotations

from urllib.parse import urlsplit

MAX_NUMBER = 2**31 - 1


class PaperlessValueError(ValueError):
    """A Paperless URL or number that is not acceptable."""


def normalize_base_url(raw: str) -> str:
    """The validated base URL without trailing slash; '' when not configured. Raises PaperlessValueError."""
    value = (raw or "").strip()
    if not value:
        return ""
    if any(ch.isspace() or ord(ch) < 32 for ch in value):
        raise PaperlessValueError("Die Paperless-Adresse darf keine Leer- oder Steuerzeichen enthalten.")
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise PaperlessValueError("Die Paperless-Adresse muss mit http:// oder https:// und einem Rechnernamen beginnen.")
    if parts.username or parts.password or "@" in parts.netloc:
        raise PaperlessValueError("Die Paperless-Adresse darf keine Zugangsdaten enthalten.")
    if parts.query or parts.fragment or "?" in value or "#" in value:
        raise PaperlessValueError("Die Paperless-Adresse darf keine Parameter (?…, #…) enthalten.")
    return value.rstrip("/")


def parse_number(raw, *, label: str) -> int | None:
    """A Paperless document number or ASN from a form field; None for an empty field."""
    text = ("" if raw is None else str(raw)).strip()
    if not text:
        return None
    if not text.isdigit() or not text.isascii():
        raise PaperlessValueError(f"{label}: bitte nur eine Zahl eingeben.")
    number = int(text)
    if number < 1 or number > MAX_NUMBER:
        raise PaperlessValueError(f"{label}: die Zahl liegt ausserhalb des gültigen Bereichs.")
    return number


def document_link(base_url: str, document_id: int | None) -> str:
    """Link to the document's detail page, or '' without base URL or number."""
    if not base_url or document_id is None:
        return ""
    if not isinstance(document_id, int) or isinstance(document_id, bool) or not 1 <= document_id <= MAX_NUMBER:
        return ""
    return f"{base_url}/documents/{document_id}/details"
