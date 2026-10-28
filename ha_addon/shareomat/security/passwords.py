# -*- coding: utf-8 -*-
"""
File: shareomat/security/passwords.py

Purpose:
    Password hashing (Argon2id) and the password policy for login accounts.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Argon2id with the OWASP minimum (19 MiB memory, 2 iterations,
    parallelism 1) and a random 16-byte salt per hash (argon2-cffi).
    Policy: at least 12 characters, passphrases welcome, no composition
    rules and no forced periodic change (NIST SP 800-63B / OWASP);
    obviously weak choices are refused. Plaintext passwords are never
    stored or logged.
"""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LENGTH = 12
MAX_LENGTH = 256

_hasher = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1, hash_len=32, salt_len=16)

# A hash of a random password: verified against when the username doesn't exist,
# so a wrong username takes as long as a wrong password (no account probing by timing).
_DUMMY_HASH = _hasher.hash("shareomat-dummy-password-for-timing")

_WEAK = {
    "passwortpasswort", "passwordpassword", "123456789012", "1234567890123", "qwertzuiopü", "qwertyuiopasdf",
    "shareomatshareomat", "adminadminadmin", "willkommen123", "sommer2026sommer", "winter2026winter",
    "passwort1234", "password1234", "hallohallohallo", "123456123456", "abcdefghijkl", "leg-one-leg-one",
}


class PasswordPolicyError(ValueError):
    """A new password was refused; the message is shown to the user."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    """Constant-effort check; also burns the same work when there is no account (password_hash None)."""
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def check_policy(password: str, *, username: str = "") -> None:
    """Raise PasswordPolicyError when a new password is too weak."""
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"Das Passwort muss mindestens {MIN_LENGTH} Zeichen lang sein. "
                                  "Tipp: ein Satz aus mehreren Wörtern ist sicher und gut zu merken.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"Das Passwort darf höchstens {MAX_LENGTH} Zeichen lang sein.")
    lowered = password.lower()
    if lowered in _WEAK or len(set(password)) <= 3:
        raise PasswordPolicyError("Dieses Passwort ist zu leicht zu erraten. Bitte ein anderes wählen.")
    if username and username.lower() in lowered:
        raise PasswordPolicyError("Das Passwort darf den Benutzernamen nicht enthalten.")
