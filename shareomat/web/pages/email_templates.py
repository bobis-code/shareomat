# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/email_templates.py

Purpose:
    "E-Mail-Vorlagen" admin page: edit the texts of the e-mails Shareomat
    will send (Willkommen, Rechnung, Gutschrift), see which placeholders a
    text may use, and preview it with example values. Sending itself comes
    later (manual and automatic delivery) and uses the same templates.

    GET  /email-templates               overview of all templates
    GET  /email-templates/<kind>        edit one template, with preview
    POST /email-templates/<kind>        save
    POST /email-templates/<kind>/reset  restore the default text

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from shareomat.core.email_templates import (
    MAX_BODY_CHARS,
    MAX_SUBJECT_CHARS,
    TEMPLATE_SPECS,
    TemplateError,
    common_values,
    document_values,
    fill_template,
    welcome_values,
)
from shareomat.database.community import get_community
from shareomat.database.contract_settings import get_contract_settings
from shareomat.database.email_templates import get_email_template, list_email_templates, reset_email_template, \
    save_email_template
from shareomat.models.invoicing import DOC_TYPE_CREDIT, DOC_TYPE_INVOICE, BillingDocument
from shareomat.security.context import current_principal
from shareomat.web.navigation import url_for
from shareomat.web.rendering import FormError, RequestContext, render_page
from shareomat.web.state import get_state

# Synthetic example values for the preview — never real participant data.
_EXAMPLE_PARTICIPANT = "Erika Muster"
_EXAMPLE_PERIOD = (date(2026, 11, 1), date(2026, 11, 30))

# A text refused on save is shown again once (per user and kind) instead of being lost.
# In memory only, at most one per user and kind, never stored.
_rejected_drafts: dict[tuple[int, str], tuple[str, str]] = {}
_MAX_REJECTED_DRAFTS = 20


# ── Private helpers ─────────────────────────────────────────────────────────


def _example_document(kind: str) -> BillingDocument:
    return BillingDocument(
        billing_run_id=0, participant_id="beispiel", number="R-2026-0001" if kind == "invoice" else "G-2026-0001",
        doc_type=DOC_TYPE_INVOICE if kind == "invoice" else DOC_TYPE_CREDIT,
        amount_chf=Decimal("23.45"), issue_date=date(2026, 12, 5), due_date=date(2027, 1, 4),
        recipient_label=_EXAMPLE_PARTICIPANT, period_start=_EXAMPLE_PERIOD[0], period_end=_EXAMPLE_PERIOD[1],
        local_kwh=Decimal("213.2"),
    )


def _example_values(ctx: RequestContext, kind: str) -> dict[str, str]:
    """Own community and representative (if entered), everything else synthetic."""
    community = get_community(ctx.db_path)
    rep = get_contract_settings(ctx.db_path)
    common = common_values(
        community.name if community and community.name else "LEG Musterdorf",
        rep.representative_name or "Max Vertreter",
        rep.representative_email or "leg@example.ch",
        _EXAMPLE_PARTICIPANT,
    )
    if kind == "welcome":
        return welcome_values(common, contract_version=5, contract_valid_from=date(2027, 1, 1),
                              joined_at=date(2027, 1, 1))
    return document_values(common, _example_document(kind))


def _kind(ctx: RequestContext) -> str | None:
    kind = ctx.segments[0] if ctx.segments else ""
    return kind if kind in TEMPLATE_SPECS else None


def _draft_key(kind: str) -> tuple[int, str] | None:
    principal = current_principal()
    return (principal.user_id, kind) if principal is not None else None


def _park_rejected(kind: str, subject: str, body: str) -> None:
    key = _draft_key(kind)
    if key is None:
        return
    if len(_rejected_drafts) >= _MAX_REJECTED_DRAFTS:
        _rejected_drafts.clear()
    _rejected_drafts[key] = (subject[:MAX_SUBJECT_CHARS * 2], body[:MAX_BODY_CHARS * 2])


def _pop_rejected(kind: str) -> tuple[str, str] | None:
    key = _draft_key(kind)
    return _rejected_drafts.pop(key, None) if key is not None else None


def _edit_url(ctx: RequestContext, kind: str) -> str:
    return url_for(ctx.ingress_path, "email_templates", suffix=f"/{kind}")


# ── Public interface ────────────────────────────────────────────────────────


def handle_get(ctx: RequestContext) -> str:
    """Overview, or one template with its placeholders and preview."""
    kind = _kind(ctx)
    if kind is None:
        templates = {t.kind: t for t in list_email_templates(ctx.db_path)}
        return render_page("email_templates/list.html", ctx, "email_templates",
                           specs=list(TEMPLATE_SPECS.values()), templates=templates)
    template = get_email_template(ctx.db_path, kind)
    preview_subject, preview_body = fill_template(template.subject, template.body, _example_values(ctx, kind))
    rejected = _pop_rejected(kind)
    return render_page(
        "email_templates/edit.html", ctx, "email_templates",
        spec=TEMPLATE_SPECS[kind], template=template, rejected=rejected,
        preview_subject=preview_subject, preview_body=preview_body,
        max_subject=MAX_SUBJECT_CHARS, max_body=MAX_BODY_CHARS,
    )


def handle_post(ctx: RequestContext) -> str | None:
    """Save or reset one template; always back to its edit page."""
    kind = _kind(ctx)
    if kind is None:
        raise FormError("Unbekannte Vorlage.")
    action = ctx.segments[1] if len(ctx.segments) > 1 else ""
    label = TEMPLATE_SPECS[kind].label
    if action == "reset":
        _pop_rejected(kind)
        reset_email_template(ctx.db_path, kind)
        get_state().set_flash(f"Vorlage «{label}»: Standardtext wiederhergestellt.", ok=True)
        return _edit_url(ctx, kind)
    if action:
        raise FormError("Unbekannte Aktion.")
    subject, body = ctx.form.get("subject", ""), ctx.form.get("body", "")
    try:
        save_email_template(ctx.db_path, kind, subject, body)
    except TemplateError as exc:
        # The stored text is unchanged; the edit page shows the refused text again.
        _park_rejected(kind, subject, body)
        get_state().set_flash(f"Nicht gespeichert: {exc}", ok=False)
        return _edit_url(ctx, kind)
    get_state().set_flash(f"Vorlage «{label}» gespeichert.", ok=True)
    return _edit_url(ctx, kind)
