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
    monkeypatch.setattr(login, "ACCOUNT_DELAY_FACTOR", 0)
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


def test_failures_slow_the_account_down_but_never_lock_the_owner_out(server):
    """Round 6 P1-02: 5+ wrong passwords must not let anybody lock the only account."""
    _setup_admin(server)
    b = Browser(server["port"])
    for _ in range(6):
        assert b.login(password="falsches Passwort 123")[0] == 401
    from shareomat.database.auth import account_delay_seconds
    assert account_delay_seconds(server["db"], "admin") > 0                   # slowed down ...
    assert Browser(server["port"]).login()[0] == 303                        # ... but the owner still gets in


def test_a_client_that_keeps_guessing_is_refused_but_others_are_not(server):
    _setup_admin(server)
    server["runtime"].web.trusted_proxies.append("127.0.0.1")              # like the HA supervisor
    try:
        attacker, owner = {"X-Forwarded-For": "10.0.0.66"}, {"X-Forwarded-For": "10.0.0.5"}
        b = Browser(server["port"])
        for _ in range(20):
            b.post("/login", {"login_csrf": b.login_csrf(), "username": "admin", "password": "falsch falsch 123"},
                   headers=attacker)
        status, _, html = b.post("/login", {"login_csrf": b.login_csrf(), "username": "admin", "password": PASSWORD},
                                 headers=attacker)
        assert status == 429 and "von diesem Gerät" in html
        o = Browser(server["port"])
        assert o.post("/login", {"login_csrf": o.login_csrf(), "username": "admin", "password": PASSWORD},
                      headers=owner)[0] == 303
    finally:
        server["runtime"].web.trusted_proxies.clear()


def test_wrong_setup_codes_from_one_client_dont_block_the_owner(server):
    """Round 6 P1-02 reproduced: wrong one-time codes used to block the setup globally for 15 minutes."""
    server["runtime"].web.trusted_proxies.append("127.0.0.1")
    try:
        code = issue_setup_code(server["db"])
        evil = Browser(server["port"])
        for _ in range(12):
            evil.post("/first-login", {"login_csrf": evil.login_csrf("/first-login"), "code": "AAAA-BBBB-CCCC",
                                       "username": "x", "password": PASSWORD, "password_repeat": PASSWORD},
                      headers={"X-Forwarded-For": "10.0.0.66"})
        owner = Browser(server["port"])
        status, _, _ = owner.post("/first-login", {"login_csrf": owner.login_csrf("/first-login"), "code": code,
                                                   "username": "admin", "password": PASSWORD,
                                                   "password_repeat": PASSWORD}, headers={"X-Forwarded-For": "10.0.0.5"})
        assert status == 303
    finally:
        server["runtime"].web.trusted_proxies.clear()


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


# ── round 6 ─────────────────────────────────────────────────────────────────


def _multipart(fields: dict, filename: str, content: bytes, boundary: str = "B0undary") -> tuple[bytes, dict]:
    crlf = chr(13) + chr(10)
    parts = b"".join((f"--{boundary}{crlf}Content-Disposition: form-data; name=\"{k}\"{crlf}{crlf}{v}{crlf}").encode()
                     for k, v in fields.items())
    parts += (f"--{boundary}{crlf}Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"{crlf}"
              f"Content-Type: text/csv{crlf}{crlf}").encode() + content + f"{crlf}--{boundary}--{crlf}".encode()
    return parts, {"Content-Type": f"multipart/form-data; boundary={boundary}"}


def test_upload_with_a_wrong_csrf_token_never_touches_the_inbox(server):
    """Round 6 P1-01 reproduced: a refused upload used to overwrite an existing inbox file."""
    a = _setup_admin(server)
    inbox = server["runtime"].paths.inbox
    (inbox / "readings.csv").write_text("ORIGINAL_VALID_CONTENT", encoding="utf-8")
    body, headers = _multipart({"csrf_token": "wrong"}, "readings.csv", b"ATTACKER_REPLACEMENT")
    status, _, _ = a.request("POST", "/meter-data/upload", body=body, headers=headers)
    assert status == 303
    assert sorted(p.name for p in inbox.iterdir()) == ["readings.csv"]
    assert (inbox / "readings.csv").read_text(encoding="utf-8") == "ORIGINAL_VALID_CONTENT"
    assert "Sicherheits-Token" in a.get("/meter-data")[2]
    body, headers = _multipart({"csrf_token": a.page_csrf()}, "readings.csv", b"SECOND_FILE")
    a.request("POST", "/meter-data/upload", body=body, headers=headers)
    assert (inbox / "readings.csv").read_text(encoding="utf-8") == "ORIGINAL_VALID_CONTENT"   # never overwritten
    assert (inbox / "readings-2.csv").read_text(encoding="utf-8") == "SECOND_FILE"


def test_parallel_uploads_of_the_same_name_never_overwrite_each_other(server):
    """Round 7 P1-V7-01: parallel uploads used to pick the same free name and replace each other."""
    import threading
    a = _setup_admin(server)
    inbox = server["runtime"].paths.inbox
    (inbox / "readings.csv").write_text("ORIGINAL", encoding="utf-8")
    csrf = a.page_csrf()
    count = 8
    barrier = threading.Barrier(count)
    statuses = []

    def upload(i: int) -> None:
        body, headers = _multipart({"csrf_token": csrf}, "readings.csv", f"DATA_{i}".encode())
        barrier.wait()
        statuses.append(a.request("POST", "/meter-data/upload", body=body, headers=headers)[0])

    threads = [threading.Thread(target=upload, args=(i,)) for i in range(count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [303] * count
    names = sorted(p.name for p in inbox.iterdir())
    assert names == sorted(["readings.csv"] + [f"readings-{n}.csv" for n in range(2, count + 2)])   # no temp left
    contents = {(inbox / n).read_text(encoding="utf-8") for n in names}
    assert contents == {"ORIGINAL"} | {f"DATA_{i}" for i in range(count)}


@pytest.mark.parametrize("hard_links", [True, False])
def test_publish_upload_skips_a_name_taken_in_the_meantime(tmp_path, monkeypatch, hard_links):
    """The name is claimed atomically; a file appearing between two tries is never replaced."""
    import os
    from shareomat.web import server as server_module
    if not hard_links:
        def no_link(*_args):
            raise OSError("hard links not supported")
        monkeypatch.setattr(server_module.os, "link", no_link)
    (tmp_path / "readings.csv").write_text("FIRST", encoding="utf-8")
    (tmp_path / "readings-2.csv").write_text("SECOND", encoding="utf-8")
    temp = tmp_path / ".upload.tmp"
    temp.write_text("THIRD", encoding="utf-8")
    dest = server_module._publish_upload(temp, tmp_path, "readings.csv")
    assert dest.name == "readings-3.csv" and dest.read_text(encoding="utf-8") == "THIRD"
    assert not temp.exists() and len(os.listdir(tmp_path)) == 3
    assert (tmp_path / "readings.csv").read_text(encoding="utf-8") == "FIRST"


def test_publish_upload_fallback_leaves_no_empty_file_if_replace_fails(tmp_path, monkeypatch):
    """Round 8 P2-V8-01: a failed replace after the O_EXCL reservation left an empty readings.csv behind."""
    import pathlib
    from shareomat.web import server as server_module

    def no_link(*_args):
        raise OSError("hard links not supported")

    def broken_replace(self, _target):
        raise OSError("disk error")

    monkeypatch.setattr(server_module.os, "link", no_link)
    temp = tmp_path / ".upload.tmp"
    temp.write_text("DATA", encoding="utf-8")
    monkeypatch.setattr(pathlib.Path, "replace", broken_replace)
    with pytest.raises(OSError, match="disk error"):
        server_module._publish_upload(temp, tmp_path, "readings.csv")
    assert not (tmp_path / "readings.csv").exists()


# ── live HA test 0.6.0: "Die Seite war zu lange offen" ──────────────────────


def test_a_malformed_foreign_cookie_does_not_hide_the_login_cookie(server):
    """Another service on the HA host may set odd cookies; SimpleCookie used to drop all cookies then."""
    b = Browser(server["port"])
    token = b.login_csrf("/first-login")
    login_cookie = "; ".join(f"{k}={v}" for k, v in b.cookies.items())
    raw = f'weird cookie=x; lonelyflag; other={{"a":1}}; {login_cookie}'
    b.cookies = {}
    code = issue_setup_code(server["db"])
    status, headers, _ = b.post("/first-login", {"login_csrf": token, "code": code, "username": "admin",
                                                 "password": PASSWORD, "password_repeat": PASSWORD},
                                headers={"Cookie": raw})
    assert status == 303 and headers["Location"] == "/"


def test_login_answers_with_exactly_one_set_cookie(server):
    """HA Ingress keeps only one Set-Cookie per response; it must be the session cookie."""
    _setup_admin(server)
    b = Browser(server["port"])
    status, _, _ = b.login()
    assert status == 303
    assert len(b.last_set_cookie) == 1 and b.last_set_cookie[0].startswith("shareomat_session=")
    assert b.get("/account")[0] == 200


def test_origin_differing_from_forwarded_host_behind_ha_is_accepted(server, monkeypatch):
    """Own reverse proxy / external URL in front of HA: the token protects, the host may differ."""
    monkeypatch.setattr(login, "HA_SUPERVISOR_IP", "127.0.0.1")
    b = Browser(server["port"])
    token = b.login_csrf("/first-login")
    code = issue_setup_code(server["db"])
    status, _, _ = b.post("/first-login", {"login_csrf": token, "code": code, "username": "admin",
                                           "password": PASSWORD, "password_repeat": PASSWORD},
                          headers={"Origin": "https://ha.example.ch", "X-Forwarded-Host": "192.168.1.5:8123"})
    assert status == 303
    b2 = Browser(server["port"])
    status, _, html = b2.post("/login", {"login_csrf": "forged", "username": "admin", "password": PASSWORD},
                              headers={"Origin": "https://evil.example"})
    assert status == 400 and "zu lange offen" in html                 # the token is still required


def test_origin_null_is_accepted_only_behind_ha(server, monkeypatch):
    """Live test 0.6.1: HA sends Referrer-Policy no-referrer, so Ingress form posts carry Origin: null."""
    _setup_admin(server)
    null = {"Origin": "null"}
    b = Browser(server["port"])
    token = b.login_csrf()
    assert b.post("/login", {"login_csrf": token, "username": "admin", "password": PASSWORD},
                  headers=null)[0] == 400                               # standalone: refused
    monkeypatch.setattr(login, "HA_SUPERVISOR_IP", "127.0.0.1")
    token = b.login_csrf()
    assert b.post("/login", {"login_csrf": token, "username": "admin", "password": PASSWORD},
                  headers=null)[0] == 303
    csrf = b.page_csrf()
    assert b.post("/logout", {"csrf_token": csrf}, headers=null)[0] == 303
    assert b.get("/account")[0] == 303                                  # logged out: the POST was accepted


def test_cookie_path_is_the_ingress_path_only_behind_the_supervisor(server):
    """Round 6 P1-03: behind HA Ingress the session cookie must not reach other add-ons' paths."""
    _setup_admin(server)
    ingress = {"X-Ingress-Path": "/api/hassio_ingress/shareomat-secret"}
    b = Browser(server["port"])
    b.get("/login", headers=ingress)                                  # untrusted peer: header ignored
    assert all("Path=/;" in c for c in b.last_set_cookie)
    server["runtime"].web.trusted_proxies.append("127.0.0.1")
    try:
        b = Browser(server["port"])
        csrf = re.search(r'name="login_csrf" value="([^"]+)"', b.get("/login", headers=ingress)[2]).group(1)
        assert all("Path=/api/hassio_ingress/shareomat-secret/;" in c for c in b.last_set_cookie)
        b.post("/login", {"login_csrf": csrf, "username": "admin", "password": PASSWORD}, headers=ingress)
        session = next(c for c in b.last_set_cookie if c.startswith(login.SESSION_COOKIE))
        assert "Path=/api/hassio_ingress/shareomat-secret/;" in session
        token = re.search(r'name="csrf_token" value="([^"]+)"', b.get("/account", headers=ingress)[2]).group(1)
        b.post("/logout", {"csrf_token": token}, headers=ingress)
        cleared = next(c for c in b.last_set_cookie if c.startswith(login.SESSION_COOKIE))
        assert "Max-Age=0" in cleared and "Path=/api/hassio_ingress/shareomat-secret/;" in cleared
    finally:
        server["runtime"].web.trusted_proxies.clear()


def test_public_forms_are_size_limited(server):
    """Round 6 P2-04: /login and /first-login refuse oversized bodies with 413 before reading them."""
    b = Browser(server["port"])
    status, _, _ = b.request("POST", "/login", body=b"x" * 20000,
                             headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert status == 413
