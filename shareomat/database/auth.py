# -*- coding: utf-8 -*-
"""
File: shareomat/database/auth.py

Purpose:
    Login accounts, server-side sessions, login-attempt throttling and the
    one-time setup code for the first password.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    - Sessions: the cookie carries a random 256-bit token; only its
      SHA-256 is stored. A session ends at its absolute expiry, after
      inactivity (unless "angemeldet bleiben"), on logout, or when revoked
      (password change, "andere Geräte abmelden", account disabled).
    - Each session has its own CSRF token and its own flash message.
    - Throttling (sliding window, never a permanent lock): a CLIENT that
      fails too often is refused for a while; the ACCOUNT itself is never
      blocked — repeated failures only make each further attempt slower.
      So nobody can lock the owner out by guessing wrong on purpose, and
      the one-time setup code can't be blocked globally either.
    - Setup code: while no account exists (or a login reset was requested
      in the add-on options), a short-lived one-time code is written to the
      log. Only its hash is stored. Without it nobody can create the first
      account, so a fresh installation is never open.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from shareomat.database.audit import record_event
from shareomat.database.sqlite import connect, now_iso
from shareomat.security.passwords import check_policy, hash_password, needs_rehash, verify_password

logger = logging.getLogger(__name__)

SESSION_HOURS = 12              # normal login: absolute lifetime
SESSION_IDLE_MINUTES = 30       # normal login: ends after this much inactivity
REMEMBER_DAYS = 30              # "Auf diesem Gerät angemeldet bleiben"
RECENT_AUTH_MINUTES = 10        # bank-account changes need a password entered this recently
ATTEMPT_WINDOW_MINUTES = 15
SLOWDOWN_AFTER_USER_FAILURES = 5     # account: from here on every attempt waits a little longer
MAX_USER_DELAY_SECONDS = 10
MAX_FAILURES_PER_CLIENT = 20         # login: this client is refused for the rest of the window
MAX_SETUP_FAILURES_PER_CLIENT = 10   # one-time code
SETUP_CODE_HOURS = 24
_TOUCH_SECONDS = 60             # last_seen is written at most once a minute


class AuthError(Exception):
    """A login/account action was refused; the message is safe to show."""


@dataclass
class User:
    id: int
    username: str
    display_name: str
    role: str
    enabled: bool
    password_changed_at: datetime


@dataclass
class Session:
    id: int
    user: User
    csrf_token: str
    remember: bool
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    auth_at: datetime
    user_agent: str
    flash_msg: str
    flash_ok: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _row_user(r) -> User:
    return User(id=r["id"], username=r["username"], display_name=r["display_name"], role=r["role"],
                enabled=bool(r["enabled"]), password_changed_at=_ts(r["password_changed_at"]))


# ── accounts ────────────────────────────────────────────────────────────────


def has_users(db_path: Path) -> bool:
    with connect(db_path) as conn:
        return conn.execute("SELECT 1 FROM auth_users LIMIT 1").fetchone() is not None


def get_user(db_path: Path, user_id: int) -> User | None:
    with connect(db_path) as conn:
        r = conn.execute("SELECT * FROM auth_users WHERE id = ?", (user_id,)).fetchone()
    return _row_user(r) if r else None


def first_admin(db_path: Path) -> User | None:
    with connect(db_path) as conn:
        r = conn.execute("SELECT * FROM auth_users WHERE role = 'admin' ORDER BY id LIMIT 1").fetchone()
    return _row_user(r) if r else None


def _check_username(username: str) -> str:
    username = username.strip()
    if not (3 <= len(username) <= 40) or any(c.isspace() for c in username):
        raise AuthError("Benutzername: 3–40 Zeichen, ohne Leerzeichen.")
    return username


def create_admin(db_path: Path, username: str, password: str, *, display_name: str = "") -> User:
    """Create the (first) administrator account."""
    username = _check_username(username)
    check_policy(password, username=username)
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            cur = conn.execute(
                "INSERT INTO auth_users (username, display_name, role, enabled, password_hash, password_changed_at, "
                "created_at, updated_at) VALUES (?, ?, 'admin', 1, ?, ?, ?, ?)",
                (username, display_name.strip(), hash_password(password), now, now, now))
            record_event(conn, "auth_user", cur.lastrowid, "created", f"Administrator {username}")
    return get_user(db_path, cur.lastrowid)


def set_password(db_path: Path, user_id: int, new_password: str, *, keep_session_id: int | None = None,
                 username: str | None = None) -> None:
    """Set a new password (optionally a new username) and end every other session of the user."""
    user = get_user(db_path, user_id)
    if user is None:
        raise AuthError("Konto nicht gefunden.")
    username = _check_username(username) if username else user.username
    check_policy(new_password, username=username)
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE auth_users SET username = ?, password_hash = ?, password_changed_at = ?, "
                         "updated_at = ? WHERE id = ?", (username, hash_password(new_password), now, now, user_id))
            conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL "
                         "AND id IS NOT ?", (now, user_id, keep_session_id))
            record_event(conn, "auth_user", user_id, "password_changed", "andere Sitzungen beendet")


def verify_user_password(db_path: Path, user_id: int, password: str) -> bool:
    with connect(db_path) as conn:
        r = conn.execute("SELECT password_hash FROM auth_users WHERE id = ?", (user_id,)).fetchone()
    return verify_password(r["password_hash"] if r else None, password)


# ── throttling ──────────────────────────────────────────────────────────────


def _record_attempt(conn, username: str, client: str, kind: str, success: bool) -> None:
    conn.execute("INSERT INTO auth_attempts (attempted_at, username, client, kind, success) VALUES (?, ?, ?, ?, ?)",
                 (now_iso(), username.lower()[:80], client[:80], kind, int(success)))


def _recent_failures(db_path: Path, kind: str, column: str, value: str) -> int:
    since = (_now() - timedelta(minutes=ATTEMPT_WINDOW_MINUTES)).isoformat()
    with connect(db_path) as conn:
        return conn.execute(
            f"SELECT COUNT(*) FROM auth_attempts WHERE kind = ? AND success = 0 AND attempted_at >= ? AND {column} = ?",
            (kind, since, value[:80])).fetchone()[0]


def throttled(db_path: Path, client: str, *, kind: str = "login") -> bool:
    """This client failed too often recently (sliding window). Other clients are not affected."""
    limit = MAX_SETUP_FAILURES_PER_CLIENT if kind == "setup" else MAX_FAILURES_PER_CLIENT
    return _recent_failures(db_path, kind, "client", client) >= limit


def account_delay_seconds(db_path: Path, username: str) -> float:
    """Slow-down for an account under attack: grows with recent failures, capped; never a lock."""
    failures = _recent_failures(db_path, "login", "username", username.lower())
    extra = failures - SLOWDOWN_AFTER_USER_FAILURES + 1
    return float(min(2 * extra, MAX_USER_DELAY_SECONDS)) if extra > 0 else 0.0


def authenticate_password(db_path: Path, username: str, password: str, client: str) -> User | None:
    """Check username/password; records the attempt. None for any failure (no hint which part was wrong)."""
    with connect(db_path) as conn:
        r = conn.execute("SELECT * FROM auth_users WHERE username = ?", (username.strip(),)).fetchone()
    ok = verify_password(r["password_hash"] if r else None, password) and bool(r and r["enabled"])
    with connect(db_path) as conn:
        with conn:
            _record_attempt(conn, username.strip(), client, "login", ok)
            if ok and needs_rehash(r["password_hash"]):
                conn.execute("UPDATE auth_users SET password_hash = ? WHERE id = ?", (hash_password(password), r["id"]))
            if ok:
                record_event(conn, "auth_user", r["id"], "login", "")
    return _row_user(r) if ok else None


# ── sessions ────────────────────────────────────────────────────────────────


def create_session(db_path: Path, user_id: int, *, remember: bool, user_agent: str = "") -> tuple[str, Session]:
    """New random session token (returned once, for the cookie) and its stored session."""
    token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + (timedelta(days=REMEMBER_DAYS) if remember else timedelta(hours=SESSION_HOURS))
    with connect(db_path) as conn:
        with conn:
            cur = conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, csrf_token, remember, created_at, last_seen_at, "
                "expires_at, auth_at, user_agent) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (token_hash(token), user_id, secrets.token_urlsafe(32), int(remember), now.isoformat(),
                 now.isoformat(), expires.isoformat(), now.isoformat(), user_agent[:200]))
    return token, get_session_by_id(db_path, cur.lastrowid)


def _row_session(r) -> Session:
    user = User(id=r["user_id"], username=r["username"], display_name=r["display_name"], role=r["role"],
                enabled=bool(r["enabled"]), password_changed_at=_ts(r["password_changed_at"]))
    return Session(id=r["id"], user=user, csrf_token=r["csrf_token"], remember=bool(r["remember"]),
                   created_at=_ts(r["created_at"]), last_seen_at=_ts(r["last_seen_at"]),
                   expires_at=_ts(r["expires_at"]), auth_at=_ts(r["auth_at"]), user_agent=r["user_agent"],
                   flash_msg=r["flash_msg"], flash_ok=bool(r["flash_ok"]))


_SESSION_SELECT = """
    SELECT s.*, u.username, u.display_name, u.role, u.enabled, u.password_changed_at
    FROM auth_sessions s JOIN auth_users u ON u.id = s.user_id
"""


def get_session_by_id(db_path: Path, session_id: int) -> Session | None:
    with connect(db_path) as conn:
        r = conn.execute(_SESSION_SELECT + " WHERE s.id = ?", (session_id,)).fetchone()
    return _row_session(r) if r else None


def resolve_session(db_path: Path, token: str) -> Session | None:
    """The valid session for a cookie token, or None (unknown, revoked, expired, idle, account disabled)."""
    if not token or len(token) > 200:
        return None
    with connect(db_path) as conn:
        r = conn.execute(_SESSION_SELECT + " WHERE s.token_hash = ? AND s.revoked_at IS NULL",
                         (token_hash(token),)).fetchone()
    if r is None:
        return None
    session = _row_session(r)
    now = _now()
    idle_limit = session.expires_at if session.remember else session.last_seen_at + timedelta(minutes=SESSION_IDLE_MINUTES)
    if not session.user.enabled or now >= session.expires_at or now >= idle_limit:
        return None
    if (now - session.last_seen_at).total_seconds() >= _TOUCH_SECONDS:
        with connect(db_path) as conn:
            with conn:
                conn.execute("UPDATE auth_sessions SET last_seen_at = ? WHERE id = ?", (now.isoformat(), session.id))
        session.last_seen_at = now
    return session


def revoke_session(db_path: Path, session_id: int) -> None:
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                         (now_iso(), session_id))


def revoke_other_sessions(db_path: Path, user_id: int, keep_session_id: int) -> int:
    with connect(db_path) as conn:
        with conn:
            n = conn.execute("UPDATE auth_sessions SET revoked_at = ? WHERE user_id = ? AND id != ? "
                             "AND revoked_at IS NULL", (now_iso(), user_id, keep_session_id)).rowcount
            record_event(conn, "auth_user", user_id, "sessions_revoked", f"{n} andere Sitzung(en)")
    return n


def list_sessions(db_path: Path, user_id: int) -> list[Session]:
    """Active sessions of a user, newest first (for "Mein Konto")."""
    now = _now().isoformat()
    with connect(db_path) as conn:
        rows = conn.execute(_SESSION_SELECT + " WHERE s.user_id = ? AND s.revoked_at IS NULL AND s.expires_at > ? "
                            "ORDER BY s.last_seen_at DESC", (user_id, now)).fetchall()
    return [_row_session(r) for r in rows]


def mark_recent_auth(db_path: Path, session_id: int) -> None:
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE auth_sessions SET auth_at = ? WHERE id = ?", (now_iso(), session_id))


def set_session_flash(db_path: Path, session_id: int, message: str, ok: bool) -> None:
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE auth_sessions SET flash_msg = ?, flash_ok = ? WHERE id = ?",
                         (message, int(ok), session_id))


def pop_session_flash(db_path: Path, session_id: int) -> tuple[str, bool]:
    with connect(db_path) as conn:
        with conn:
            r = conn.execute("SELECT flash_msg, flash_ok FROM auth_sessions WHERE id = ?", (session_id,)).fetchone()
            if r is None or not r["flash_msg"]:
                return "", True
            conn.execute("UPDATE auth_sessions SET flash_msg = '', flash_ok = 1 WHERE id = ?", (session_id,))
    return r["flash_msg"], bool(r["flash_ok"])


# ── one-time setup code ─────────────────────────────────────────────────────


def _state_get(conn, key: str) -> str | None:
    r = conn.execute("SELECT value FROM auth_state WHERE key = ?", (key,)).fetchone()
    return r["value"] if r else None


def _state_set(conn, key: str, value: str | None) -> None:
    if value is None:
        conn.execute("DELETE FROM auth_state WHERE key = ?", (key,))
    else:
        conn.execute("INSERT INTO auth_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, value))


def setup_needed(db_path: Path) -> bool:
    """No account yet, or a login reset was requested (add-on option "reset_login")."""
    if not has_users(db_path):
        return True
    with connect(db_path) as conn:
        return _state_get(conn, "reset_requested") == "1"


def request_login_reset(db_path: Path) -> None:
    """Allow setting a new password with a fresh setup code (the old password keeps working until then)."""
    with connect(db_path) as conn:
        with conn:
            _state_set(conn, "reset_requested", "1")
            record_event(conn, "auth", "setup", "reset_requested", "über Add-on-Option")


def issue_setup_code(db_path: Path) -> str | None:
    """Create a fresh one-time code (logged, never stored in clear) when setup is needed. Returns it or None."""
    if not setup_needed(db_path):
        with connect(db_path) as conn:
            with conn:
                _state_set(conn, "setup_code_hash", None)
                _state_set(conn, "setup_code_expires", None)
        return None
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(12))
    code = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
    expires = _now() + timedelta(hours=SETUP_CODE_HOURS)
    with connect(db_path) as conn:
        with conn:
            _state_set(conn, "setup_code_hash", token_hash(code))
            _state_set(conn, "setup_code_expires", expires.isoformat())
    logger.warning("=" * 64)
    logger.warning("Shareomat Ersteinrichtung: Einmal-Code  %s  (gültig bis %s UTC)", code,
                   expires.strftime("%d.%m.%Y %H:%M"))
    logger.warning("Im Browser Shareomat öffnen und den Code eingeben, um das Passwort festzulegen.")
    logger.warning("=" * 64)
    return code


def setup_code_valid(db_path: Path, code: str) -> bool:
    with connect(db_path) as conn:
        stored = _state_get(conn, "setup_code_hash")
        expires = _ts(_state_get(conn, "setup_code_expires"))
    normalized = code.strip().upper().replace(" ", "")
    if len(normalized) == 12 and "-" not in normalized:
        normalized = f"{normalized[:4]}-{normalized[4:8]}-{normalized[8:]}"
    return bool(stored and expires and _now() < expires
                and hmac.compare_digest(stored, token_hash(normalized)))


def complete_setup(db_path: Path, code: str, username: str, password: str, client: str) -> User:
    """Use the one-time code: create the administrator, or (login reset) set a new password for it."""
    if throttled(db_path, client, kind="setup"):
        raise AuthError("Zu viele Versuche. Bitte in 15 Minuten erneut versuchen.")
    if not setup_needed(db_path) or not setup_code_valid(db_path, code):
        with connect(db_path) as conn:
            with conn:
                _record_attempt(conn, "__setup__", client, "setup", False)
        raise AuthError("Der Einmal-Code ist ungültig oder abgelaufen. Den aktuellen Code finden Sie im "
                        "Protokoll (Log) des Add-ons; nach einem Neustart gilt ein neuer Code.")
    admin = first_admin(db_path)
    if admin is None:
        user = create_admin(db_path, username, password)
    else:
        set_password(db_path, admin.id, password, username=username)
        user = get_user(db_path, admin.id)
    with connect(db_path) as conn:
        with conn:
            _state_set(conn, "setup_code_hash", None)
            _state_set(conn, "setup_code_expires", None)
            _state_set(conn, "reset_requested", None)
            _record_attempt(conn, "__setup__", client, "setup", True)
            record_event(conn, "auth", "setup", "completed", f"Konto {user.username}")
    return user
