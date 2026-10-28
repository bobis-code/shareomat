# -*- coding: utf-8 -*-
"""
File: shareomat/security/context.py

Purpose:
    The authenticated principal of the current web request (thread-local)
    and the actor recorded in the audit log.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The web server handles each request in its own thread
    (ThreadingHTTPServer), so a thread-local is exactly "this request".
    Outside a web request (scheduled runs, imports, CLI) there is no
    principal and the audit actor is "system".
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

_local = threading.local()


@dataclass
class Principal:
    """Who is making the current request, as established by the login session."""

    user_id: int
    username: str
    display_name: str
    role: str
    session_id: int
    csrf_token: str
    auth_at: datetime
    remember: bool = False
    flash: dict = field(default_factory=dict)   # {"msg": str, "ok": bool} — persisted per session

    @property
    def label(self) -> str:
        return self.display_name or self.username

    @property
    def actor(self) -> str:
        return f"user:{self.user_id}:{self.username}"


def set_principal(principal: Principal | None) -> None:
    _local.principal = principal


def current_principal() -> Principal | None:
    return getattr(_local, "principal", None)


def current_actor() -> str:
    principal = current_principal()
    return principal.actor if principal is not None else "system"
