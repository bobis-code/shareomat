# -*- coding: utf-8 -*-
"""S-05: the administrator-entered tariff URL must only reach public https:// servers."""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from shareomat.external_data import safe_http
from shareomat.external_data.safe_http import (
    SafeFetchError,
    UnsafeUrlError,
    fetch_public_https,
    resolve_public_addresses,
    validate_public_https_url,
)
from shareomat.external_data.vnb_tariff_json import (
    VnbTariffFetchError,
    VnbTariffFormatError,
    fetch_vnb_tariff_json,
    parse_vnb_tariff_json,
)

PUBLIC_V4 = "93.184.215.14"
PUBLIC_V6 = "2606:2800:21f:cb07:6820:80da:af6b:8b2c"
EBL_SAMPLE = Path(__file__).resolve().parents[1] / "docs" / "Energieversorgungsunternehmen" / "EBL" / "EBL_2027.json"


# ── helpers ──────────────────────────────────────────────────────────────────


def _addrinfo(*addresses: str):
    infos = []
    for address in addresses:
        if ":" in address:
            infos.append((socket.AF_INET6, socket.SOCK_STREAM, 6, "", (address, 443, 0, 0)))
        else:
            infos.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443)))
    return infos


@pytest.fixture
def dns(monkeypatch):
    """host -> list of addresses; also records every lookup."""
    table: dict[str, list[str]] = {}
    lookups: list[str] = []

    def fake_getaddrinfo(host, port, *args, **kwargs):
        lookups.append(host)
        if host not in table:
            raise socket.gaierror("unknown host")
        return _addrinfo(*table[host])

    monkeypatch.setattr(safe_http.socket, "getaddrinfo", fake_getaddrinfo)
    table["lookups"] = lookups  # type: ignore[assignment]
    return table


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, chunk=None):
        self.status = status
        self._body = body
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}
        self._chunk = chunk

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, size):
        if self._chunk:
            size = min(size, self._chunk)
        data, self._body = self._body[:size], self._body[size:]
        return data


class FakeServer:
    """Replaces the pinned HTTPS connection; maps host -> list of responses."""

    def __init__(self):
        self.routes: dict[str, list[FakeResponse]] = {}
        self.requests: list[tuple[str, list, str, dict]] = []
        self.closed = 0

    def __call__(self, host, addresses, timeout):
        server = self

        class Conn:
            def request(self, method, target, headers):
                server.requests.append((host, addresses, target, headers))
                self._host = host

            def getresponse(self):
                return server.routes[self._host].pop(0)

            def close(self):
                server.closed += 1

        return Conn()


@pytest.fixture
def server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(safe_http, "_open_connection", fake)
    return fake


def _fetch(url, **kwargs):
    kwargs.setdefault("max_bytes", 1024)
    return fetch_public_https(url, accept="application/json", user_agent="test", **kwargs)


# ── URL syntax: scheme, credentials, port ────────────────────────────────────


@pytest.mark.parametrize("url", [
    "http://www.ebl.ch/hubfs/EBL%202027.json",
    "file:///etc/passwd",
    "file://C:/Windows/win.ini",
    "ftp://example.ch/tarif.json",
    "data:application/json,{}",
    "javascript:alert(1)",
    "gopher://example.ch/",
    "//www.ebl.ch/tarif.json",
    "www.ebl.ch/tarif.json",
    "",
    "   ",
])
def test_only_https_scheme_is_accepted(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_https_url(url)


@pytest.mark.parametrize("url", [
    "https://user:pass@www.ebl.ch/t.json",
    "https://user@www.ebl.ch/t.json",
    "https://www.ebl.ch:8443/t.json",
    "https://www.ebl.ch:80/t.json",
    "https://www.ebl.ch:99999/t.json",
    "https://www.ebl.ch:abc/t.json",
    "https:///t.json",
    "https://www.ebl.ch/t.json\r\nX-Injected: 1",
    "https://www.ebl.ch/t .json",
    "https://www.ebl.ch/" + "a" * 3000,
])
def test_credentials_ports_and_control_characters_are_rejected(url):
    with pytest.raises(UnsafeUrlError):
        validate_public_https_url(url)


@pytest.mark.parametrize("url", [
    "https://www.ebl.ch/hubfs/EBL%202027.json",
    "https://www.ebl.ch:443/hubfs/EBL%202027.json",
    "HTTPS://WWW.EBL.CH/hubfs/EBL%202027.json",
    "  https://www.ebl.ch/hubfs/EBL%202027.json  ",
])
def test_legitimate_tariff_urls_pass_syntax_check(url):
    assert validate_public_https_url(url).hostname == "www.ebl.ch"


# ── address checks: no internal targets ──────────────────────────────────────


@pytest.mark.parametrize("address", [
    "127.0.0.1", "127.1.2.3", "10.0.0.5", "172.16.0.1", "192.168.1.10",
    "169.254.169.254",          # cloud metadata
    "100.64.0.1",               # carrier-grade NAT / Tailscale
    "0.0.0.0", "224.0.0.1", "240.0.0.1", "255.255.255.255",
    "::1", "fe80::1", "fc00::1", "fd12:3456::1", "::", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:192.168.1.1",
])
def test_internal_addresses_are_refused(dns, server, address):
    dns["internal.example"] = [address]
    with pytest.raises(UnsafeUrlError):
        _fetch("https://internal.example/t.json")
    assert server.requests == []


@pytest.mark.parametrize("literal", [
    "https://127.0.0.1/t.json", "https://[::1]/t.json", "https://169.254.169.254/latest/meta-data",
    "https://192.168.1.1/t.json", "https://[::ffff:10.0.0.1]/t.json",
])
def test_ip_literals_are_checked_like_host_names(monkeypatch, server, literal):
    # Real getaddrinfo on an IP literal returns the literal without DNS.
    with pytest.raises(UnsafeUrlError):
        _fetch(literal)
    assert server.requests == []


def test_any_internal_address_in_a_mixed_answer_is_refused(dns, server):
    dns["mixed.example"] = [PUBLIC_V4, "10.1.1.1"]
    with pytest.raises(UnsafeUrlError):
        _fetch("https://mixed.example/t.json")
    assert server.requests == []


def test_unresolvable_host_is_a_fetch_error(dns, server):
    with pytest.raises(SafeFetchError):
        _fetch("https://does-not-exist.example/t.json")


def test_connection_is_pinned_to_the_validated_addresses(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4, PUBLIC_V6]
    server.routes["www.ebl.ch"] = [FakeResponse(200, b"{}")]
    assert _fetch("https://www.ebl.ch/hubfs/EBL%202027.json") == b"{}"
    host, addresses, target, headers = server.requests[0]
    assert host == "www.ebl.ch"
    assert [a[1][0] for a in addresses] == [PUBLIC_V4, PUBLIC_V6]
    assert target == "/hubfs/EBL%202027.json"
    assert headers["Accept-Encoding"] == "identity"
    assert dns["lookups"] == ["www.ebl.ch"]  # exactly one lookup, no rebinding window


def test_pinned_connection_connects_to_ip_but_verifies_host_name(monkeypatch):
    connected, wrapped = [], []

    class FakeSocket:
        def __init__(self, family, kind):
            self.family = family

        def settimeout(self, value):
            pass

        def connect(self, sockaddr):
            connected.append(sockaddr)

        def close(self):
            pass

    class FakeContext:
        def wrap_socket(self, sock, server_hostname):
            wrapped.append(server_hostname)
            return sock

    monkeypatch.setattr(safe_http.socket, "socket", FakeSocket)
    conn = safe_http._PinnedHTTPSConnection(
        "www.ebl.ch", [(socket.AF_INET, (PUBLIC_V4, 443))], timeout=5, context=FakeContext(),
    )
    conn.connect()
    assert connected == [(PUBLIC_V4, 443)]
    assert wrapped == ["www.ebl.ch"]


def test_production_connection_verifies_certificates():
    conn = safe_http._open_connection("www.ebl.ch", [(socket.AF_INET, (PUBLIC_V4, 443))], 5)
    assert conn._tls_context.verify_mode == safe_http.ssl.CERT_REQUIRED
    assert conn._tls_context.check_hostname is True


def test_resolve_public_addresses_returns_public_answers(dns):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    assert resolve_public_addresses("www.ebl.ch") == [(socket.AF_INET, (PUBLIC_V4, 443))]


# ── redirects ────────────────────────────────────────────────────────────────


def test_public_https_redirect_is_followed_and_revalidated(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    dns["cdn.example"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(302, headers={"Location": "https://cdn.example/f.json"})]
    server.routes["cdn.example"] = [FakeResponse(200, b'{"ok": 1}')]
    assert _fetch("https://www.ebl.ch/hubfs/x.json") == b'{"ok": 1}'
    assert [r[0] for r in server.requests] == ["www.ebl.ch", "cdn.example"]


def test_relative_redirect_is_resolved_against_current_url(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [
        FakeResponse(301, headers={"Location": "/hubfs/neu.json"}),
        FakeResponse(200, b"{}"),
    ]
    _fetch("https://www.ebl.ch/hubfs/alt.json")
    assert server.requests[1][2] == "/hubfs/neu.json"


@pytest.mark.parametrize("location", [
    "http://www.ebl.ch/hubfs/x.json",           # downgrade
    "file:///etc/passwd",
    "https://127.0.0.1/admin",
    "https://internal.example/x",
    "https://www.ebl.ch:8080/x",
    "https://user:pw@www.ebl.ch/x",
])
def test_unsafe_redirect_targets_are_refused(dns, server, location):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    dns["internal.example"] = ["192.168.0.10"]
    server.routes["www.ebl.ch"] = [FakeResponse(302, headers={"Location": location})]
    with pytest.raises(UnsafeUrlError):
        _fetch("https://www.ebl.ch/hubfs/x.json")
    assert len(server.requests) == 1


def test_redirect_loop_is_limited(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(302, headers={"Location": "/again"}) for _ in range(10)]
    with pytest.raises(SafeFetchError, match="Weiterleitungen"):
        _fetch("https://www.ebl.ch/start")
    assert len(server.requests) == safe_http._MAX_REDIRECTS + 1


def test_redirect_without_location_is_an_error(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(302)]
    with pytest.raises(SafeFetchError):
        _fetch("https://www.ebl.ch/x")


# ── response limits ──────────────────────────────────────────────────────────


def test_declared_oversize_response_is_refused_before_reading(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(200, b"x" * 10, headers={"Content-Length": "999999"})]
    with pytest.raises(SafeFetchError, match="grösser"):
        _fetch("https://www.ebl.ch/x", max_bytes=1000)


def test_streamed_oversize_response_is_refused(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(200, b"x" * 5000, chunk=100)]  # no Content-Length
    with pytest.raises(SafeFetchError, match="grösser"):
        _fetch("https://www.ebl.ch/x", max_bytes=1000)


def test_response_of_exactly_max_bytes_is_accepted(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(200, b"x" * 1000, chunk=333)]
    assert len(_fetch("https://www.ebl.ch/x", max_bytes=1000)) == 1000


@pytest.mark.parametrize("value", ["abc", "-1x"])
def test_invalid_content_length_is_refused(dns, server, value):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(200, b"{}", headers={"Content-Length": value})]
    with pytest.raises(SafeFetchError):
        _fetch("https://www.ebl.ch/x")


def test_compressed_response_is_refused(dns, server):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(200, b"\x1f\x8b", headers={"Content-Encoding": "gzip"})]
    with pytest.raises(SafeFetchError, match="Inhaltskodierung"):
        _fetch("https://www.ebl.ch/x")


@pytest.mark.parametrize("status", [204, 403, 404, 500])
def test_non_200_status_is_an_error(dns, server, status):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(status, b"<html>internal</html>")]
    with pytest.raises(SafeFetchError, match=str(status)):
        _fetch("https://www.ebl.ch/x")


def test_total_time_budget_is_enforced(dns, server, monkeypatch):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    server.routes["www.ebl.ch"] = [FakeResponse(200, b"x" * 500, chunk=10)]
    clock = iter(range(0, 10_000, 7))
    monkeypatch.setattr(safe_http.time, "monotonic", lambda: next(clock))
    with pytest.raises(SafeFetchError, match="Zeitlimit"):
        _fetch("https://www.ebl.ch/x", total_timeout=30)


def test_network_errors_become_fetch_errors_and_close_connection(dns, monkeypatch):
    dns["www.ebl.ch"] = [PUBLIC_V4]
    closed = []

    class Failing:
        def request(self, *a, **k):
            raise ConnectionResetError("reset")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(safe_http, "_open_connection", lambda *a: Failing())
    with pytest.raises(SafeFetchError, match="reset"):
        _fetch("https://www.ebl.ch/x")
    assert closed == [True]


# ── tariff import on top of the safe fetcher ─────────────────────────────────


def _serve(monkeypatch, body: bytes, status=200, headers=None):
    monkeypatch.setattr(safe_http, "resolve_public_addresses",
                        lambda host: [(socket.AF_INET, (PUBLIC_V4, 443))])
    fake = FakeServer()
    fake.routes["www.ebl.ch"] = [FakeResponse(status, body, headers)]
    monkeypatch.setattr(safe_http, "_open_connection", fake)
    return fake


@pytest.mark.skipif(not EBL_SAMPLE.exists(), reason="real EBL tariff file is excluded from Git (.gitignore)")
def test_real_ebl_tariff_file_still_imports(monkeypatch):
    _serve(monkeypatch, EBL_SAMPLE.read_bytes())
    operator, products = parse_vnb_tariff_json(fetch_vnb_tariff_json("https://www.ebl.ch/hubfs/EBL%202027.json"))
    assert operator == "EBL"
    assert len(products) >= 1
    assert all(p.energy_rate_ht_chf_kwh is not None for p in products)


def test_utf8_bom_is_accepted(monkeypatch):
    _serve(monkeypatch, b"\xef\xbb\xbf" + b'{"operator": "EBL"}')
    assert isinstance(fetch_vnb_tariff_json("https://www.ebl.ch/t.json"), dict)


@pytest.mark.parametrize("url", ["http://www.ebl.ch/t.json", "file:///etc/passwd", "https://127.0.0.1/t.json"])
def test_unsafe_tariff_url_is_reported_as_fetch_error(url):
    with pytest.raises(VnbTariffFetchError, match="nicht erlaubt"):
        fetch_vnb_tariff_json(url)


def test_oversize_tariff_file_is_refused(monkeypatch):
    from shareomat.external_data import vnb_tariff_json

    monkeypatch.setattr(vnb_tariff_json, "_MAX_RESPONSE_BYTES", 100)
    _serve(monkeypatch, b"{" + b" " * 500 + b"}")
    with pytest.raises(VnbTariffFetchError, match="grösser"):
        fetch_vnb_tariff_json("https://www.ebl.ch/t.json")


@pytest.mark.parametrize("body", [b"\xff\xfe not utf8", b"{not json", b"<html>login</html>"])
def test_invalid_tariff_body_is_a_format_error(monkeypatch, body):
    _serve(monkeypatch, body)
    with pytest.raises(VnbTariffFormatError):
        fetch_vnb_tariff_json("https://www.ebl.ch/t.json")


def test_deeply_nested_json_is_a_format_error(monkeypatch):
    _serve(monkeypatch, b"[" * 200_000 + b"]" * 200_000)
    with pytest.raises(VnbTariffFormatError, match="verschachtelt"):
        fetch_vnb_tariff_json("https://www.ebl.ch/t.json")


@pytest.mark.parametrize("payload", [
    [], "text", 42, {"dsoName": "EBL"}, {"tariffs": []},
    {"dsoName": "EBL", "tariffs": "x"}, {"dsoName": 5, "tariffs": []},
    {"dsoName": "EBL", "tariffs": [{"tariffType": "electricity", "tariffName": "X",
                                    "startDate": "2027-01-01", "prices": {"energy": [
                                        {"price": "NaN", "priceUnit": "CHF/kWh"}]}}]},
])
def test_manipulated_tariff_content_is_refused(monkeypatch, payload):
    _serve(monkeypatch, json.dumps(payload).encode())
    with pytest.raises(VnbTariffFormatError):
        parse_vnb_tariff_json(fetch_vnb_tariff_json("https://www.ebl.ch/t.json"))


def test_web_import_shows_unsafe_url_as_form_error():
    from shareomat.web.pages import tariffs
    from shareomat.web.rendering import FormError, RequestContext

    ctx = RequestContext(method="POST", ingress_path="", form={"url": "http://192.168.1.1/tarif.json"}, query={})
    ctx.segments = ["vnb", "import"]
    with pytest.raises(FormError, match="nicht erlaubt"):
        tariffs.handle_post(ctx)
