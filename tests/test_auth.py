# -*- coding: utf-8 -*-
"""Login and access control, tested through a real HTTP server (shareomat.web.login / database.auth)."""

from __future__ import annotations

import http.client
import re
import threading
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.audit import list_events
from shareomat.database.auth import issue_setup_code, request_login_reset
from shareomat.database.invoicing import get_payment_profile
from shareomat.database.sqlite import connect, init_db
from shareomat.web import login
from shareomat.web.navigation import ROUTE_PATHS
from shareomat.web.server import _Handler
from shareomat.web.state import get_state

PASSWORD = "Sonne scheint aufs Dach 2026"
IBAN = "CH93 0076 2011 6238 5295 7"


@pytest.fixture
def server(tmp_path, monkeypatch):
    db = tmp_path / "shareomat.db"
    init_db(db)
    for n in ("inbox", "archive", "reports", "state"):
        (tmp_path / n).mkdir()
    runtime = RuntimeConfig(paths=PathConfig(inbox=tmp_path / "inbox", archive=tmp_path / "archive",
                                             reports=tmp_path / "reports", state=tmp_path / "state"))
    state = get_state()
    state.register_db(db)
    state.register_runtime(runtime)
    runs = []
    state.register_on_run(lambda: runs.append(1))
    monkeypatch.setattr(login, "FAILED_LOGIN_DELAY_SECONDS", 0)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield {"port": httpd.server_address[1], "db": db, "runtime": runtime, "runs": runs, "tmp": tmp_path}
    httpd.shutdown()


class Browser:
    """A minimal browser: keeps cookies, never follows redirects."""

    def __init__(self, port: int):
        self.port = port
        self.cookies: dict[str, str] = {}
        self.last_set_cookie: list[str] = []

    def request(self, method: str, path: str, *, form: dict | None = None, body: bytes | None = None,
                headers: dict | None = None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = dict(headers or {})
        if self.cookies:
            h["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        if form is not None:
            body = urlencode(form).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(method, path, body=body, headers=h)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", errors="replace")
        self.last_set_cookie = resp.headers.get_all("Set-Cookie") or []
        for raw in self.last_set_cookie:
            name, _, rest = raw.partition("=")
            value = rest.split(";", 1)[0]
            if "Max-Age=0" in raw:
                self.cookies.pop(name, None)
            else:
                self.cookies[name] = value
        conn.close()
        return resp.status, resp.headers, text

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, form, **kw):
        return self.request("POST", path, form=form, **kw)

    def login_csrf(self, path="/login") -> str:
        _, _, html = self.get(path)
        return re.search(r'name="login_csrf" value="([^"]+)"', html).group(1)

    def page_csrf(self, path="/account") -> str:
        status, _, html = self.get(path)
        assert status == 200, status
        return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)

    def login(self, username="admin", password=PASSWORD, remember=False):
        form = {"login_csrf": self.login_csrf(), "username": username, "password": password, "next": "/"}
        if remember:
            form["remember"] = "on"
        return self.post("/login", form)


def _setup_admin(srv, browser: Browser | None = None) -> Browser:
    browser = browser or Browser(srv["port"])
    code = issue_setup_code(srv["db"])
    status, headers, _ = browser.post("/first-login", {
        "login_csrf": browser.login_csrf("/first-login"), "code": code, "username": "admin",
        "password": PASSWORD, "password_repeat": PASSWORD})
    assert status == 303 and headers["Location"] == "/", (status, _)
    return browser


def _protected_paths():
    public = {"login", "first_login", "logout", "static", "banner"}
    return [p for name, p in ROUTE_PATHS.items() if name not in public] + [
        "/reports/download?f=x.json", "/contract/participants/1/document", "/invoices/1/print",
        "/invoices/runs/1/print-all", "/invoices/reminders/1/print"]


# ── fresh installation: closed until the setup code is used ─────────────────


def test_fresh_install_is_closed_and_only_the_setup_code_opens_it(server):
    b = Browser(server["port"])
    for path in _protected_paths():
        status, headers, _ = b.get(path)
        assert status == 303 and headers["Location"] == "/first-login", path
    assert b.get("/static/app.css")[0] == 200
    status, headers, _ = b.post("/actions/run", {"csrf_token": "x"})
    assert status == 303 and server["runs"] == []

    status, _, html = b.post("/first-login", {"login_csrf": b.login_csrf("/first-login"), "code": "AAAA-BBBB-CCCC",
                                              "username": "admin", "password": PASSWORD, "password_repeat": PASSWORD})
    assert status == 400 and "ungültig oder abgelaufen" in html
    _setup_admin(server, b)
    assert b.get("/")[0] == 200                                     # logged in right away
    assert b.get("/first-login")[1]["Location"] == "/login"         # the code page is gone
    other = Browser(server["port"])
    assert other.get("/invoices")[1]["Location"].startswith("/login")


def test_setup_code_is_single_use(server):
    code = issue_setup_code(server["db"])
    b = Browser(server["port"])
    b.post("/first-login", {"login_csrf": b.login_csrf("/first-login"), "code": code, "username": "admin",
                            "password": PASSWORD, "password_repeat": PASSWORD})
    c = Browser(server["port"])
    status, headers, _ = c.post("/first-login", {"login_csrf": "x", "code": code, "username": "evil",
                                                 "password": PASSWORD, "password_repeat": PASSWORD})
    assert status == 303 and headers["Location"] == "/login"
    with connect(server["db"]) as conn:
        assert [r["username"] for r in conn.execute("SELECT username FROM auth_users")] == ["admin"]


# ── login, hashing, cookies ─────────────────────────────────────────────────


def test_login_hashes_with_argon2id_and_sets_a_safe_cookie(server):
    _setup_admin(server)
    b = Browser(server["port"])
    status, _, html = b.login(password="falsches Passwort 123")
    assert status == 401 and "Benutzername oder Passwort ist falsch" in html
    status, _, html = b.login(username="niemand", password="falsches Passwort 123")
    assert status == 401 and "Benutzername oder Passwort ist falsch" in html       # same message
    status, headers, _ = b.login()
    assert status == 303
    cookie = next(c for c in b.last_set_cookie if c.startswith(login.SESSION_COOKIE))
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie and "Max-Age" not in cookie
    token = cookie.split("=", 1)[1].split(";")[0]
    with connect(server["db"]) as conn:
        pw_hash = conn.execute("SELECT password_hash FROM auth_users").fetchone()[0]
        stored = [r[0] for r in conn.execute("SELECT token_hash FROM auth_sessions")]
    assert pw_hash.startswith("$argon2id$") and "m=19456,t=2,p=1" in pw_hash
    assert token not in stored and len(token) >= 40
    raw = server["db"].read_bytes()
    assert PASSWORD.encode() not in raw and token.encode() not in raw


def test_secure_cookie_behind_a_trusted_https_proxy(server):
    _setup_admin(server)
    server["runtime"].web.trusted_proxies.append("127.0.0.1")
    b = Browser(server["port"])
    b.login()
    plain = next(c for c in b.last_set_cookie if c.startswith(login.SESSION_COOKIE))
    assert "Secure" not in plain
    form = {"login_csrf": b.login_csrf(), "username": "admin", "password": PASSWORD}
    b.post("/login", form, headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in next(c for c in b.last_set_cookie if c.startswith(login.SESSION_COOKIE))
    server["runtime"].web.trusted_proxies.clear()


def test_too_many_failures_throttle_even_the_right_password(server):
    _setup_admin(server)
    b = Browser(server["port"])
    for _ in range(5):
        assert b.login(password="falsches Passwort 123")[0] == 401
    status, _, html = b.login()
    assert status == 429 and "Zu viele Fehlversuche" in html


# ── every route needs a session ─────────────────────────────────────────────


def test_every_route_needs_a_login_session(server):
    _setup_admin(server)
    b = Browser(server["port"])
    for path in _protected_paths():
        status, headers, _ = b.get(path)
        assert status == 303 and headers["Location"].startswith("/login"), path


def test_posts_without_session_have_no_side_effects(server):
    _setup_admin(server)
    b = Browser(server["port"])
    boundary = "XyZ"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"csrf_token\"\r\n\r\nx\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"evil.csv\"\r\n"
            f"Content-Type: text/csv\r\n\r\na,b\r\n--{boundary}--\r\n").encode()
    status, headers, _ = b.request("POST", "/meter-data/upload", body=body,
                                   headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert status == 303 and headers["Location"] == "/login"
    assert list(server["runtime"].paths.inbox.iterdir()) == []
    assert b.post("/actions/run", {"csrf_token": "x"})[0] == 303 and server["runs"] == []
    b.post("/invoices/settings", {"payee": "Evil", "iban": IBAN, "postal_code": "1", "city": "X"})
    assert get_payment_profile(server["db"]).payee == ""
    assert b.post("/logout", {"csrf_token": "x"})[1]["Location"] == "/login"


def test_ingress_headers_never_authenticate(server):
    _setup_admin(server)
    b = Browser(server["port"])
    status, headers, _ = b.get("/invoices", headers={"X-Ingress-Path": "/api/hassio_ingress/abc",
                                                     "X-Remote-User-Id": "123", "X-Remote-User-Name": "admin"})
    assert status == 303 and headers["Location"].startswith("/api/hassio_ingress/abc/login")


# ── sessions: separate CSRF and messages, logout, revoke, expiry ───────────


def test_two_devices_have_separate_csrf_tokens_and_messages(server):
    a = _setup_admin(server)
    b = Browser(server["port"])
    b.login()
    token_a, token_b = a.page_csrf(), b.page_csrf()
    assert token_a != token_b
    form = {"payee": "LEG Konto", "street": "Weg", "building": "1", "postal_code": "4000", "city": "Basel",
            "country": "CH", "iban": IBAN}
    b.post("/invoices/settings", {**form, "csrf_token": token_a})            # A's token in B's session
    assert get_payment_profile(server["db"]).payee == ""
    _, _, html_b = b.get("/invoices?tab=settings")
    assert "Sicherheits-Token" in html_b
    _, _, html_a = a.get("/invoices?tab=settings")
    assert "Sicherheits-Token" not in html_a                                  # B's message stays with B
    b.post("/invoices/settings", {**form, "csrf_token": token_b})
    assert get_payment_profile(server["db"]).payee == "LEG Konto"


def test_logout_and_revoking_other_devices(server):
    a = _setup_admin(server)
    b, c = Browser(server["port"]), Browser(server["port"])
    b.login()
    c.login()
    a.post("/logout", {"csrf_token": a.page_csrf()})
    assert a.get("/")[1]["Location"].startswith("/login")
    assert b.get("/")[0] == 200
    b.post("/account/revoke-others", {"csrf_token": b.page_csrf()})
    assert c.get("/")[1]["Location"].startswith("/login")
    assert b.get("/")[0] == 200


def test_logout_needs_the_session_token(server):
    a = _setup_admin(server)
    a.post("/logout", {"csrf_token": "foreign"})
    assert a.get("/")[0] == 200


def test_remember_me_and_idle_expiry(server):
    _setup_admin(server)
    keep, short = Browser(server["port"]), Browser(server["port"])
    keep.login(remember=True)
    assert "Max-Age=2592000" in next(c for c in keep.last_set_cookie if c.startswith(login.SESSION_COOKIE))
    short.login()
    old = (datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat()
    with connect(server["db"]) as conn:
        with conn:
            conn.execute("UPDATE auth_sessions SET last_seen_at = ?", (old,))
    assert short.get("/")[1]["Location"].startswith("/login")       # 30 min idle -> logged out
    assert keep.get("/")[0] == 200                                    # "angemeldet bleiben" survives idling


def test_password_change_ends_other_sessions(server):
    a = _setup_admin(server)
    b = Browser(server["port"])
    b.login()
    new = "Neues Passwort für unsere LEG"
    a.post("/account/password", {"csrf_token": a.page_csrf(), "current_password": "falsch falsch falsch",
                                 "new_password": new, "new_password_repeat": new})
    assert "bisherige Passwort stimmt nicht" in a.get("/account")[2]
    a.post("/account/password", {"csrf_token": a.page_csrf(), "current_password": PASSWORD,
                                 "new_password": "kurz", "new_password_repeat": "kurz"})
    assert "mindestens 12 Zeichen" in a.get("/account")[2]
    a.post("/account/password", {"csrf_token": a.page_csrf(), "current_password": PASSWORD,
                                 "new_password": new, "new_password_repeat": new})
    assert a.get("/")[0] == 200 and b.get("/")[1]["Location"].startswith("/login")
    assert Browser(server["port"]).login()[0] == 401
    assert Browser(server["port"]).login(password=new)[0] == 303


def test_foreign_origin_is_refused(server):
    a = _setup_admin(server)
    status, _, html = a.post("/account/revoke-others", {"csrf_token": a.page_csrf()},
                             headers={"Origin": "https://evil.example"})
    assert status == 403 and "nicht von einer Shareomat-Seite" in html
    b = Browser(server["port"])
    status, _, _ = b.post("/login", {"login_csrf": b.login_csrf(), "username": "admin", "password": PASSWORD},
                          headers={"Origin": "https://evil.example"})
    assert status == 400 and login.SESSION_COOKIE not in b.cookies


def test_security_headers(server):
    a = _setup_admin(server)
    _, headers, _ = a.get("/")
    assert headers["Cache-Control"] == "no-store" and headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors" in headers["Content-Security-Policy"]
    _, headers, _ = a.get("/static/app.css")
    assert "public" in headers["Cache-Control"]


# ── bank account changes, audit, forgotten password ─────────────────────────


def test_bank_account_change_needs_a_recent_password(server):
    a = _setup_admin(server)
    old = (datetime.now(timezone.utc) - timedelta(minutes=11)).isoformat()
    with connect(server["db"]) as conn:
        with conn:
            conn.execute("UPDATE auth_sessions SET auth_at = ?", (old,))
    form = {"payee": "LEG Konto", "street": "Weg", "building": "1", "postal_code": "4000", "city": "Basel",
            "country": "CH", "iban": IBAN}
    _, _, html = a.get("/invoices?tab=settings")
    assert 'name="confirm_password"' in html
    a.post("/invoices/settings", {**form, "csrf_token": a.page_csrf()})
    assert get_payment_profile(server["db"]).payee == ""
    a.post("/invoices/settings", {**form, "csrf_token": a.page_csrf(), "confirm_password": PASSWORD})
    assert get_payment_profile(server["db"]).payee == "LEG Konto"
    events = list_events(server["db"], entity="payment_profile")
    assert events and events[0].actor == "user:1:admin"


def test_login_reset_with_a_new_code(server):
    _setup_admin(server)
    request_login_reset(server["db"])
    code = issue_setup_code(server["db"])
    assert Browser(server["port"]).login()[0] == 303                 # old password still works until reset
    b = Browser(server["port"])
    new = "Ganz neues Passwort für Shareomat"
    status, _, _ = b.post("/first-login", {"login_csrf": b.login_csrf("/first-login"), "code": code,
                                           "username": "admin", "password": new, "password_repeat": new})
    assert status == 303
    assert Browser(server["port"]).login()[0] == 401
    assert Browser(server["port"]).login(password=new)[0] == 303
    assert Browser(server["port"]).get("/first-login")[1]["Location"] == "/login"


def test_unknown_post_routes_and_downloads_stay_closed_after_login_check(server):
    """Logged in: unknown paths are 404, not served by accident; open redirects after login are refused."""
    a = _setup_admin(server)
    assert a.get("/does-not-exist")[0] == 404
    b = Browser(server["port"])
    form = {"login_csrf": b.login_csrf(), "username": "admin", "password": PASSWORD, "next": "//evil.example/x"}
    assert b.post("/login", form)[1]["Location"] == "/"
