# -*- coding: utf-8 -*-
"""
File: shareomat/database/email_templates.py

Purpose:
    Read, save and reset the e-mail templates. Reuses the generic
    `settings` key/value table (no new table, no schema change), like
    contract_settings.py: two keys per kind, "email_template.<kind>.subject"
    and "email_template.<kind>.body". Without stored keys the default text
    from shareomat.core.email_templates applies.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Every save and reset is in the audit log. Only known kinds are
    accepted, and the text is validated before it is written.
"""

from __future__ import annotations

from pathlib import Path

from shareomat.core.email_templates import TEMPLATE_SPECS, spec_for, validate_template
from shareomat.database.audit import record_event
from shareomat.database.community import ensure_community_row_id
from shareomat.database.sqlite import connect, now_iso
from shareomat.models.email_template import EmailTemplate

_AUDIT_ENTITY = "email_template"


# ── Private helpers ─────────────────────────────────────────────────────────


def _keys(kind: str) -> tuple[str, str]:
    spec_for(kind)  # unknown kind -> KeyError, never an arbitrary settings key
    return f"email_template.{kind}.subject", f"email_template.{kind}.body"


# ── Public functions ────────────────────────────────────────────────────────


def get_email_template(db_path: Path, kind: str) -> EmailTemplate:
    """The stored template of a kind, or its default text."""
    spec = spec_for(kind)
    subject_key, body_key = _keys(kind)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT setting_key, setting_value, updated_at FROM settings WHERE setting_key IN (?, ?)",
            (subject_key, body_key),
        ).fetchall()
    stored = {r["setting_key"]: r for r in rows}
    if subject_key not in stored or body_key not in stored:
        return EmailTemplate(kind=kind, subject=spec.default_subject, body=spec.default_body, is_default=True)
    return EmailTemplate(
        kind=kind,
        subject=stored[subject_key]["setting_value"] or "",
        body=stored[body_key]["setting_value"] or "",
        is_default=False,
        updated_at=max(stored[subject_key]["updated_at"], stored[body_key]["updated_at"]),
    )


def list_email_templates(db_path: Path) -> list[EmailTemplate]:
    """All known kinds, in the order of TEMPLATE_SPECS."""
    return [get_email_template(db_path, kind) for kind in TEMPLATE_SPECS]


def save_email_template(db_path: Path, kind: str, subject: str, body: str) -> EmailTemplate:
    """Validate and store a template (raises TemplateError for a text that can not be stored)."""
    subject, body = validate_template(kind, subject, body)
    subject_key, body_key = _keys(kind)
    now = now_iso()
    with connect(db_path) as conn:
        with conn:
            community_row_id = ensure_community_row_id(conn)
            for key, value in ((subject_key, subject), (body_key, body)):
                conn.execute(
                    """
                    INSERT INTO settings (community_id, setting_key, setting_value, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(community_id, setting_key)
                    DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
                    """,
                    (community_row_id, key, value, now),
                )
            record_event(conn, _AUDIT_ENTITY, kind, "saved", f"Betreff: {subject}")
    return get_email_template(db_path, kind)


def reset_email_template(db_path: Path, kind: str) -> EmailTemplate:
    """Remove the stored text, so the default applies again."""
    subject_key, body_key = _keys(kind)
    with connect(db_path) as conn:
        with conn:
            conn.execute("DELETE FROM settings WHERE setting_key IN (?, ?)", (subject_key, body_key))
            record_event(conn, _AUDIT_ENTITY, kind, "reset", "Standardtext wiederhergestellt")
    return get_email_template(db_path, kind)
