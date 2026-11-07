# -*- coding: utf-8 -*-
"""
File: shareomat/external_data/safe_http.py

Purpose:
    Download a small document from a PUBLIC https:// address that an
    administrator typed in (S-05). Used for the grid operator's tariff JSON.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Rules, checked again for every redirect target:
      * only https, no user:password@, only the default port 443;
      * the host name is resolved ONCE; every resolved address must be a
        public (global unicast) address, and the TCP connection goes to
        exactly that validated address (no second DNS lookup, so DNS
        rebinding cannot switch to an internal target afterwards);
      * TLS certificate and host name are verified (system trust store);
      * at most _MAX_REDIRECTS redirects, never to http or a non-public host;
      * response body limited to max_bytes, no transparent decompression,
        overall time budget.
    No HTTP(S) proxy is used. Fixed, built-in endpoints (BFE, ElCom,
    ENTSO-E, SNB) are not routed through this module.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from urllib.parse import SplitResult, urljoin, urlsplit

_MAX_REDIRECTS = 3
_MAX_URL_LENGTH = 2048
_HTTPS_PORT = 443
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class UnsafeUrlError(ValueError):
    """The address is not an allowed public https:// address."""


class SafeFetchError(Exception):
    """The document could not be downloaded within the limits."""


def validate_public_https_url(url: str) -> SplitResult:
    """Check scheme, credentials, port and host syntax; no network access."""
    if not isinstance(url, str) or not url.strip():
        raise UnsafeUrlError("Adresse fehlt.")
    url = url.strip()
    if len(url) > _MAX_URL_LENGTH or any(ch.isspace() or ord(ch) < 0x20 for ch in url):
        raise UnsafeUrlError("Adresse ist zu lang oder enthält Leer-/Steuerzeichen.")
    parts = urlsplit(url)
    if parts.scheme.lower() != "https":
        raise UnsafeUrlError("Nur https://-Adressen sind erlaubt.")
    if parts.username is not None or parts.password is not None:
        raise UnsafeUrlError("Zugangsdaten in der Adresse sind nicht erlaubt.")
    try:
        port = parts.port
    except ValueError as exc:
        raise UnsafeUrlError("Ungültiger Port in der Adresse.") from exc
    if port not in (None, _HTTPS_PORT):
        raise UnsafeUrlError("Nur der Standardport 443 ist erlaubt.")
    if not parts.hostname:
        raise UnsafeUrlError("Adresse enthält keinen Servernamen.")
    return parts


def _is_public(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_global and not (
        ip.is_multicast or ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_unspecified
    )


def resolve_public_addresses(host: str) -> list[tuple[int, tuple]]:
    """Resolve once; refuse if ANY address is not public (mixed answers included)."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:  # IP literal: checked directly, no DNS involved
        if not _is_public(host):
            raise UnsafeUrlError("Interne oder nicht öffentliche IP-Adressen sind nicht erlaubt.")
        if literal.version == 6:
            return [(socket.AF_INET6, (host, _HTTPS_PORT, 0, 0))]
        return [(socket.AF_INET, (host, _HTTPS_PORT))]
    try:
        infos = socket.getaddrinfo(host, _HTTPS_PORT, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError) as exc:
        raise SafeFetchError(f"Servername {host!r} konnte nicht aufgelöst werden.") from exc
    addresses: list[tuple[int, tuple]] = []
    for family, _type, _proto, _canon, sockaddr in infos:
        if family not in (socket.AF_INET, socket.AF_INET6):
            continue
        try:
            public = _is_public(sockaddr[0])
        except ValueError as exc:
            raise UnsafeUrlError(f"Server {host!r} liefert eine ungültige Adresse.") from exc
        if not public:
            raise UnsafeUrlError(
                f"Server {host!r} zeigt auf eine nicht öffentliche Adresse; interne Ziele sind nicht erlaubt."
            )
        if (family, sockaddr) not in addresses:
            addresses.append((family, sockaddr))
    if not addresses:
        raise SafeFetchError(f"Servername {host!r} hat keine verwendbare Adresse.")
    return addresses


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS to an already validated IP; SNI and certificate check use the host name."""

    def __init__(self, host: str, addresses: list[tuple[int, tuple]], *, timeout: float,
                 context: ssl.SSLContext) -> None:
        super().__init__(host, _HTTPS_PORT, timeout=timeout, context=context)
        self._addresses = addresses
        self._tls_context = context

    def connect(self) -> None:
        last_error: OSError | None = None
        for family, sockaddr in self._addresses:
            sock = socket.socket(family, socket.SOCK_STREAM)
            try:
                sock.settimeout(self.timeout)
                sock.connect(sockaddr)
                self.sock = self._tls_context.wrap_socket(sock, server_hostname=self.host)
                return
            except OSError as exc:
                sock.close()
                last_error = exc
        raise last_error or OSError("Keine Verbindung möglich")


def _open_connection(host: str, addresses: list[tuple[int, tuple]], timeout: float):
    """Seam for tests; production always verifies certificates and host names."""
    return _PinnedHTTPSConnection(host, addresses, timeout=timeout, context=ssl.create_default_context())


def fetch_public_https(
    url: str, *, max_bytes: int, accept: str, user_agent: str,
    timeout: float = 20, total_timeout: float = 60,
) -> bytes:
    """GET a public https:// document; every hop is validated, the body is bounded."""
    deadline = time.monotonic() + total_timeout
    current = url
    for _hop in range(_MAX_REDIRECTS + 1):
        parts = validate_public_https_url(current)
        addresses = resolve_public_addresses(parts.hostname)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SafeFetchError("Zeitlimit beim Abruf überschritten.")
        conn = _open_connection(parts.hostname, addresses, min(timeout, remaining))
        try:
            target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
            conn.request("GET", target, headers={
                "User-Agent": user_agent, "Accept": accept, "Accept-Encoding": "identity",
            })
            response = conn.getresponse()
            if response.status in _REDIRECT_STATUSES:
                location = response.getheader("Location")
                if not location:
                    raise SafeFetchError(f"Weiterleitung ohne Zieladresse (HTTP {response.status}).")
                current = urljoin(current, location.strip())
                continue
            if response.status != 200:
                raise SafeFetchError(f"Server antwortet mit HTTP {response.status}.")
            encoding = (response.getheader("Content-Encoding") or "identity").strip().lower()
            if encoding != "identity":
                raise SafeFetchError(f"Nicht unterstützte Inhaltskodierung: {encoding}.")
            declared = response.getheader("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise SafeFetchError("Ungültige Längenangabe des Servers.") from exc
                if declared_size > max_bytes:
                    raise SafeFetchError(f"Datei ist grösser als erlaubt (max. {max_bytes} Bytes).")
            body = bytearray()
            while True:
                if time.monotonic() > deadline:
                    raise SafeFetchError("Zeitlimit beim Abruf überschritten.")
                chunk = response.read(min(64 * 1024, max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise SafeFetchError(f"Datei ist grösser als erlaubt (max. {max_bytes} Bytes).")
            return bytes(body)
        except (OSError, http.client.HTTPException) as exc:
            raise SafeFetchError(f"Abruf fehlgeschlagen: {exc}") from exc
        finally:
            conn.close()
    raise SafeFetchError(f"Zu viele Weiterleitungen (max. {_MAX_REDIRECTS}).")
