# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/account.py

Purpose:
    "Mein Konto": change the password and see/end the logged-in devices.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Routes (relative to /account):
        GET  /                    account and devices
        POST /password            change password (current one required), ends the other devices
        POST /revoke-others       log out every other device (e.g. lost phone)
"""

from __future__ import annotations

from shareomat.database.auth import AuthError, list_sessions, revoke_other_sessions, set_password, verify_user_password
from shareomat.security.context import current_principal
from shareomat.security.passwords import PasswordPolicyError
from shareomat.web.navigation import url_for
from shareomat.web.rendering import FormError, Redirect, RequestContext, render_page
from shareomat.web.state import get_state


def _device(user_agent: str) -> str:
    ua = user_agent.lower()
    system = next((name for key, name in (("iphone", "iPhone"), ("ipad", "iPad"), ("android", "Android"),
                                         ("windows", "Windows"), ("mac os", "Mac"), ("linux", "Linux"),
                                         ("home assistant", "Home-Assistant-App")) if key in ua), "Gerät")
    browser = next((name for key, name in (("edg/", "Edge"), ("firefox", "Firefox"), ("chrome", "Chrome"),
                                          ("safari", "Safari")) if key in ua), "")
    return f"{system} {browser}".strip()


def handle_get(ctx: RequestContext) -> str:
    principal = current_principal()
    if principal is None:
        raise Redirect(url_for(ctx.ingress_path, "login"))
    sessions = [{"current": s.id == principal.session_id, "device": _device(s.user_agent), "session": s}
                for s in list_sessions(ctx.db_path, principal.user_id)]
    return render_page("account/view.html", ctx, "account", principal=principal, sessions=sessions)


def handle_post(ctx: RequestContext) -> str | None:
    principal = current_principal()
    if principal is None:
        raise Redirect(url_for(ctx.ingress_path, "login"))
    back = url_for(ctx.ingress_path, "account")
    if ctx.segments == ["password"]:
        if not verify_user_password(ctx.db_path, principal.user_id, ctx.form.get("current_password", "")):
            raise FormError("Das bisherige Passwort stimmt nicht.")
        new, repeat = ctx.form.get("new_password", ""), ctx.form.get("new_password_repeat", "")
        if new != repeat:
            raise FormError("Die beiden neuen Passwörter sind nicht gleich.")
        try:
            set_password(ctx.db_path, principal.user_id, new, keep_session_id=principal.session_id)
        except (AuthError, PasswordPolicyError) as exc:
            raise FormError(str(exc))
        get_state().set_flash("Passwort geändert. Alle anderen Geräte wurden abgemeldet.", ok=True)
        raise Redirect(back)
    if ctx.segments == ["revoke-others"]:
        n = revoke_other_sessions(ctx.db_path, principal.user_id, principal.session_id)
        get_state().set_flash(f"{n} andere(s) Gerät(e) abgemeldet." if n else "Keine anderen Geräte angemeldet.", ok=True)
        raise Redirect(back)
    raise FormError("Unbekannte Aktion.")
