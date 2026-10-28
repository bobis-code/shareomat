# -*- coding: utf-8 -*-
"""
File: shareomat/web/login.py

Purpose:
    Login, first-time setup, logout and the central access gate of the
    web server. Every request except the static files and these login
    pages goes through `gate()` BEFORE anything is read or executed.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    - Fail closed: no database, no account yet, no valid session or no
      permission -> no page, no download, no upload, no action.
    - Cookies: HttpOnly, SameSite=Lax, Path=/ (ingress path behind HA), Secure when the request came
      over HTTPS (X-Forwarded-Proto is only believed from the HA supervisor
      or a configured trusted proxy). The token is never in a URL.
    - X-Ingress-Path is only used to build links — it never authenticates.
      Behind Home Assistant Ingress the Shareomat login applies as well
      (for now; mapping HA users is prepared but not built).
    - CSRF: every logged-in session has its own token (forms) and POSTs with
      a foreign Origin are refused; the login form has its own double-submit
      token.
"""

from __future__ import annotations

import ipaddress
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from shareomat.database.auth import (
    RECENT_AUTH_MINUTES,
    account_delay_seconds,
    REMEMBER_DAYS,
    AuthError,
    authenticate_password,
    complete_setup,
    create_session,
    has_users,
    issue_setup_code,
    mark_recent_auth,
    resolve_session,
    revoke_session,
    setup_needed,
    throttled,
    verify_user_password,
)
from shareomat.security.context import Principal, set_principal
from shareomat.security.passwords import PasswordPolicyError
from shareomat.security.permissions import PERMISSIONS, has_permission, required_permission
from shareomat.web.navigation import url_for
from shareomat.web.rendering import _env as jinja_env
from shareomat.web.state import get_state

logger = logging.getLogger(__name__)

SESSION_COOKIE = "shareomat_session"
LOGIN_CSRF_COOKIE = "shareomat_login_csrf"
HA_SUPERVISOR_IP = "172.30.32.2"
FAILED_LOGIN_DELAY_SECONDS = 0.5     # slows guessing down; tests set it to 0
ACCOUNT_DELAY_FACTOR = 1.0           # tests set it to 0 (the growing per-account delay)

PUBLIC_PATHS = {"/login", "/first-login"}


# ── request helpers (work on the BaseHTTPRequestHandler) ────────────────────


def client_ip(handler) -> str:
    """The real client: the socket peer, or — only behind the HA supervisor or a configured trusted
    proxy — the first X-Forwarded-For entry (so all phones behind Ingress don't count as one client).

    Only safe if that proxy overwrites a client-supplied X-Forwarded-For; otherwise the per-client limit can
    be dodged (the per-account delay still applies). Needs verification: the HA supervisor's header chain."""
    peer = str(handler.client_address[0]) if getattr(handler, "client_address", None) else ""
    if peer and _peer_trusted(peer):
        forwarded = handler.headers.get("X-Forwarded-For", "").split(",")[0].strip()
        if forwarded:
            return forwarded
    return peer


def _peer_trusted(peer: str) -> bool:
    runtime = get_state().runtime
    return peer in {HA_SUPERVISOR_IP, *(runtime.web.trusted_proxies if runtime else [])}


def _peer(handler) -> str:
    return str(handler.client_address[0]) if getattr(handler, "client_address", None) else ""


def _trusted_proxy(handler) -> bool:
    return _peer_trusted(_peer(handler))


def is_https(handler) -> bool:
    if _trusted_proxy(handler):
        return handler.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower() == "https"
    return False


def _cookies(handler) -> dict[str, str]:
    """Plain `name=value; ...` parsing. SimpleCookie drops ALL cookies when a single foreign cookie on the
    same host (another add-on, another service) is malformed — under HA Ingress that silently broke the login."""
    jar: dict[str, str] = {}
    for header in handler.headers.get_all("Cookie") or []:
        for pair in header.split(";"):
            name, sep, value = pair.strip().partition("=")
            if sep and name and name not in jar:
                jar[name] = value.strip().strip('"')
    return jar


def cookie_value(handler, name: str) -> str:
    return _cookies(handler).get(name, "")


def cookie_path(handler) -> str:
    """Behind Home Assistant Ingress the cookie is limited to Shareomat's own ingress path, so the
    browser never sends it to other add-ons on the same HA site. X-Ingress-Path only counts when the
    request really came from the supervisor/trusted proxy; standalone it is "/"."""
    ingress = handler.headers.get("X-Ingress-Path", "").rstrip("/")
    if ingress and _trusted_proxy(handler) and ingress.startswith("/") and ";" not in ingress \
            and all(c.isprintable() and not c.isspace() for c in ingress):
        return ingress + "/"
    return "/"


def set_cookie(handler, name: str, value: str, *, max_age: int | None = None, same_site: str = "Lax") -> None:
    parts = [f"{name}={value}", f"Path={cookie_path(handler)}", "HttpOnly", f"SameSite={same_site}"]
    if max_age is not None:
        parts.append(f"Max-Age={max_age}")
    if is_https(handler):
        parts.append("Secure")
    handler.pending_cookies.append("; ".join(parts))


def clear_cookie(handler, name: str) -> None:
    set_cookie(handler, name, "", max_age=0)


def same_origin(handler) -> bool:
    """POSTs from another site are refused (in addition to the CSRF token)."""
    origin = handler.headers.get("Origin", "")
    if not origin or origin == "null":
        return origin != "null"
    origin_host = urlparse(origin).netloc.lower()
    expected = {handler.headers.get("Host", "").lower()}
    if _trusted_proxy(handler):
        expected.add(handler.headers.get("X-Forwarded-Host", "").split(",")[0].strip().lower())
    if origin_host in expected:
        return True
    if _peer(handler) == HA_SUPERVISOR_IP:
        # Behind HA (own reverse proxy, external URL) the forwarded host may legitimately differ from what
        # the browser shows. The CSRF tokens (and SameSite cookies) are the protection there.
        logger.info("Login: origin %s differs from forwarded host %s behind HA, relying on the CSRF token",
                    origin_host, sorted(expected))
        return True
    return False


def _safe_next(raw: str) -> str:
    """Only local paths of this app (no scheme, no //host) as redirect targets after login."""
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw or "\n" in raw or "\r" in raw:
        return "/"
    return raw


# ── pages ───────────────────────────────────────────────────────────────────


def render_standalone(template: str, ingress_path: str, **context) -> str:
    def _url(route_name: str, *, suffix: str = "") -> str:
        return url_for(ingress_path, route_name, suffix=suffix)
    return jinja_env.get_template(template).render(url_for=_url, **context)


def message_page(handler, ingress_path: str, title: str, text: str, *, status: int, link: tuple[str, str] | None = None):
    handler._send_html(render_standalone("auth/message.html", ingress_path, title=title, text=text, link=link),
                       status=status)


def _login_form(handler, ingress_path: str, *, error: str = "", username: str = "", next_path: str = "/",
                status: int = 200) -> None:
    token = secrets.token_urlsafe(24)
    set_cookie(handler, LOGIN_CSRF_COOKIE, token, max_age=3600, same_site="Strict")
    handler._send_html(render_standalone(
        "auth/login.html", ingress_path, login_csrf=token, error=error, username=username, next_path=next_path,
        remember_days=REMEMBER_DAYS, insecure=not is_https(handler) and not is_loopback(handler)),
        status=status)


def _setup_form(handler, ingress_path: str, *, error: str = "", username: str = "admin", status: int = 200) -> None:
    db_path = get_state().db_path
    token = secrets.token_urlsafe(24)
    set_cookie(handler, LOGIN_CSRF_COOKIE, token, max_age=3600, same_site="Strict")
    handler._send_html(render_standalone(
        "auth/first_login.html", ingress_path, login_csrf=token, error=error, username=username,
        reset=has_users(db_path), remember_days=REMEMBER_DAYS), status=status)


def _login_csrf_ok(handler, form: dict) -> bool:
    expected = cookie_value(handler, LOGIN_CSRF_COOKIE)
    if not expected:
        problem = "login cookie missing"
    elif not secrets.compare_digest(expected, form.get("login_csrf", "")):
        problem = "login token does not match the cookie"
    elif not same_origin(handler):
        problem = "foreign origin"
    else:
        return True
    # Names only, never values — enough to see whether the browser/Ingress delivered the cookie
    logger.warning("Login form refused: %s (cookies sent: %s, origin %s, host %s, forwarded host %s, "
                   "cookie path %s, peer %s)", problem, sorted(_cookies(handler)) or "none",
                   handler.headers.get("Origin", "-"), handler.headers.get("Host", "-"),
                   handler.headers.get("X-Forwarded-Host", "-"), cookie_path(handler), _peer(handler))
    return False


def _start_session(handler, ingress_path: str, user_id: int, remember: bool, next_path: str) -> None:
    db_path = get_state().db_path
    old = resolve_session(db_path, cookie_value(handler, SESSION_COOKIE))
    if old is not None:
        revoke_session(db_path, old.id)          # new token on every login (no session fixation)
    token, _session = create_session(db_path, user_id, remember=remember,
                                     user_agent=handler.headers.get("User-Agent", ""))
    # Exactly ONE Set-Cookie: HA Ingress passes response headers on as a plain dict, so a second cookie
    # (e.g. clearing the login token) would replace the session cookie. The login token expires by itself.
    set_cookie(handler, SESSION_COOKIE, token, max_age=REMEMBER_DAYS * 86400 if remember else None)
    handler._redirect(ingress_path + _safe_next(next_path))


def handle_public(handler, method: str, path: str, ingress_path: str, form: dict | None, query: dict) -> None:
    """GET/POST /login and /first-login (reachable without a session)."""
    db_path = get_state().db_path
    if db_path is None:
        message_page(handler, ingress_path, "Shareomat startet nicht",
                     "Die Anwendung ist nicht bereit. Details stehen im Protokoll (Log) des Add-ons.", status=503)
        return
    if path == "/first-login":
        if not setup_needed(db_path):
            handler._redirect(url_for(ingress_path, "login"))
            return
        if method == "GET":
            _setup_form(handler, ingress_path)
            return
        if not _login_csrf_ok(handler, form):
            _setup_form(handler, ingress_path, error="Die Seite war zu lange offen. Bitte erneut versuchen.", status=400)
            return
        password, repeat = form.get("password", ""), form.get("password_repeat", "")
        if password != repeat:
            _setup_form(handler, ingress_path, error="Die beiden Passwörter sind nicht gleich.",
                        username=form.get("username", ""), status=400)
            return
        try:
            user = complete_setup(db_path, form.get("code", ""), form.get("username", ""), password, client_ip(handler))
        except (AuthError, PasswordPolicyError) as exc:
            time.sleep(FAILED_LOGIN_DELAY_SECONDS)
            _setup_form(handler, ingress_path, error=str(exc), username=form.get("username", ""), status=400)
            return
        logger.info("Login: setup completed for %s", user.username)
        _start_session(handler, ingress_path, user.id, "remember" in form, "/")
        return

    # /login
    if not has_users(db_path):
        handler._redirect(url_for(ingress_path, "first_login"))
        return
    if method == "GET":
        _login_form(handler, ingress_path, next_path=_safe_next(query.get("next", "/")))
        return
    username, next_path = form.get("username", "").strip(), _safe_next(form.get("next", "/"))
    if not _login_csrf_ok(handler, form):
        _login_form(handler, ingress_path, error="Die Anmeldeseite war zu lange offen. Bitte erneut anmelden.",
                    username=username, next_path=next_path, status=400)
        return
    if throttled(db_path, client_ip(handler)):
        logger.warning("Login: client %s throttled", client_ip(handler))
        _login_form(handler, ingress_path, error="Zu viele Fehlversuche von diesem Gerät. Bitte in 15 Minuten "
                    "erneut versuchen.", username=username, next_path=next_path, status=429)
        return
    time.sleep(account_delay_seconds(db_path, username) * ACCOUNT_DELAY_FACTOR)
    user = authenticate_password(db_path, username, form.get("password", ""), client_ip(handler))
    if user is None:
        # The client address in the log shows whether Ingress tells phones apart (X-Forwarded-For check)
        logger.warning("Login: failed attempt from %s (peer %s)", client_ip(handler), handler.client_address[0])
        time.sleep(FAILED_LOGIN_DELAY_SECONDS)
        _login_form(handler, ingress_path, error="Benutzername oder Passwort ist falsch.", username=username,
                    next_path=next_path, status=401)
        return
    _start_session(handler, ingress_path, user.id, "remember" in form, next_path)


def handle_logout(handler, ingress_path: str, form: dict) -> None:
    """POST /logout — needs the session's CSRF token (a foreign page can't log you out)."""
    db_path = get_state().db_path
    session = resolve_session(db_path, cookie_value(handler, SESSION_COOKIE)) if db_path else None
    if session is not None and secrets.compare_digest(session.csrf_token, form.get("csrf_token", "")) \
            and same_origin(handler):
        revoke_session(db_path, session.id)
        clear_cookie(handler, SESSION_COOKIE)
    handler._redirect(url_for(ingress_path, "login"))


# ── the gate ────────────────────────────────────────────────────────────────


def gate(handler, method: str, path: str, ingress_path: str, route: str, segments: list[str]) -> bool:
    """Authenticate + authorize one request. True = go on (principal set); False = response already sent."""
    db_path = get_state().db_path
    if db_path is None:
        message_page(handler, ingress_path, "Shareomat startet nicht",
                     "Die Anwendung ist nicht bereit. Details stehen im Protokoll (Log) des Add-ons.", status=503)
        return False
    if not has_users(db_path):
        if not _setup_code_present(db_path):
            issue_setup_code(db_path)
        handler._redirect(url_for(ingress_path, "first_login"))
        return False
    session = resolve_session(db_path, cookie_value(handler, SESSION_COOKIE))
    if session is None:
        if cookie_value(handler, SESSION_COOKIE):
            clear_cookie(handler, SESSION_COOKIE)
        target = url_for(ingress_path, "login")
        if method == "GET":
            target += "?next=" + quote(path, safe="/-_")
        handler._redirect(target)
        return False
    if method == "POST" and not same_origin(handler):
        message_page(handler, ingress_path, "Anfrage abgelehnt",
                     "Diese Aktion kam nicht von einer Shareomat-Seite und wurde deshalb nicht ausgeführt.", status=403)
        return False
    principal = Principal(user_id=session.user.id, username=session.user.username,
                          display_name=session.user.display_name, role=session.user.role, session_id=session.id,
                          csrf_token=session.csrf_token, auth_at=session.auth_at, remember=session.remember)
    permission = required_permission(method, route, segments)
    if not has_permission(principal, permission):
        message_page(handler, ingress_path, "Keine Berechtigung",
                     f"Für «{PERMISSIONS.get(permission, permission)}» fehlt die Berechtigung. "
                     "Bitte den Administrator kontaktieren.", status=403, link=("dashboard", "Zur Übersicht"))
        return False
    set_principal(principal)
    return True


def _setup_code_present(db_path) -> bool:
    from shareomat.database.auth import _state_get
    from shareomat.database.sqlite import connect
    with connect(db_path) as conn:
        expires = _state_get(conn, "setup_code_expires")
    return bool(expires and datetime.fromisoformat(expires) > datetime.now(timezone.utc))


def recent_auth_required(principal: Principal | None) -> bool:
    """Bank-account changes need a password entered within RECENT_AUTH_MINUTES."""
    if principal is None:
        return True
    return datetime.now(timezone.utc) - principal.auth_at > timedelta(minutes=RECENT_AUTH_MINUTES)


def confirm_recent_auth(principal: Principal, password: str) -> bool:
    db_path = get_state().db_path
    if not verify_user_password(db_path, principal.user_id, password):
        return False
    mark_recent_auth(db_path, principal.session_id)
    principal.auth_at = datetime.now(timezone.utc)
    return True


def is_loopback(handler) -> bool:
    try:
        return ipaddress.ip_address(client_ip(handler)).is_loopback
    except ValueError:
        return False
