# -*- coding: utf-8 -*-
"""
File: shareomat/models/email_template.py

Purpose:
    One e-mail template (subject + plain-text body) as stored by
    shareomat.database.email_templates and filled by
    shareomat.core.email_templates.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmailTemplate:
    """Subject and body of one template kind; is_default when nothing of it is stored yet."""

    kind: str
    subject: str
    body: str
    is_default: bool = True
    updated_at: str = ""
