# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/documents.py

Purpose:
    "Dokumente → Meldungen & Mitteilungen":
    - Vertragsmitteilungen: every contract notice Shareomat sent by e-mail
      or created as a letter (Checkliste → Mitteilungen), with the e-mail /
      letter as it went out and its PDF.
    - EBL-Meldungen: the notice to the grid operator that a new participant
      joins the LEG (fields of EBL's participation form), per participant,
      with what is still missing and a printable page.

    GET /documents                        overview
    GET /documents/notices/<id>           one contract notice (e-mail or letter)
    GET /documents/notices/<id>/pdf       its PDF (served by the web server itself, see notice_pdf)
    GET /documents/ebl/<participant>      printable EBL notice (standalone page, no menu)

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Read-only. Contract notices are stored by shareomat.core.delivery.notice_mail;
    their PDF is produced again from the stored text and the published,
    unchangeable contract text (checked against the SHA-256 kept with the
    notice). The EBL print is Shareomat's own neutral layout of the form's
    content, never EBL's logo or letterhead (see
    shareomat.database.participant_documents).
"""

from __future__ import annotations

from datetime import date

from shareomat.core.delivery.pdf import render_pdf
from shareomat.database.contract_notices import get_notice, list_notices
from shareomat.database.contract_versions import get_contract_version
from shareomat.database.participant_documents import ebl_join_notice, ebl_join_notices
from shareomat.models.contract import ContractVersion
from shareomat.models.contract_notice import NOTICE_METHOD_EMAIL
from shareomat.web.rendering import FormError, RequestContext, _env as jinja_env, render_page


# ── Private helpers ─────────────────────────────────────────────────────────


def _notice_page(ctx: RequestContext, raw_id: str) -> str:
    notice = get_notice(ctx.db_path, int(raw_id)) if raw_id.isdigit() else None
    if notice is None:
        return render_page("placeholders/coming_soon.html", ctx, "documents", title="Mitteilung nicht gefunden",
                           description="Bitte erneut aus der Liste öffnen.")
    return render_page("documents/notice.html", ctx, "documents", notice=notice)


# ── Public interface ────────────────────────────────────────────────────────


def contract_pdf_page(version: ContractVersion) -> str:
    """The contract of a version exactly as a notice e-mail attaches it (same page as "Vertrag drucken")."""
    return jinja_env.get_template("contract/print.html").render(
        title=f"Gesellschaftsvertrag v{version.version}", body=version.contract_text_snapshot,
        version=version, sha256=version.text_sha256, for_pdf=True)


def notice_letter_page(version: ContractVersion, *, recipient: str, subject: str, body: str,
                       notice_date: date) -> str:
    """A notice letter: the notice text on the first page, the full contract as enclosure."""
    letter = jinja_env.get_template("contract/notice_letter_body.html").render(
        version=version, recipient=recipient, subject=subject, body=body, notice_date=notice_date)
    valid_from = f", gültig ab {version.valid_from:%d.%m.%Y}" if version.valid_from else ""
    return jinja_env.get_template("contract/print.html").render(
        title=subject, body=letter, appendix=version.contract_text_snapshot,
        appendix_caption=f"Beilage: Gesellschaftsvertrag Version {version.version}{valid_from}.",
        version=version, sha256=version.text_sha256, for_pdf=True)


def notice_pdf(db_path, raw_id: str) -> tuple[str, bytes]:
    """(file name, PDF) of a stored contract notice: the e-mail's attachment, or the letter."""
    notice = get_notice(db_path, int(raw_id)) if raw_id.isdigit() else None
    if notice is None:
        raise FormError("Mitteilung nicht gefunden.")
    version = get_contract_version(db_path, notice.contract_version_id)
    if version is None or version.text_sha256 != notice.contract_sha256:
        raise FormError("Der Vertragstext dieser Mitteilung ist nicht mehr unverändert vorhanden.")
    if notice.method == NOTICE_METHOD_EMAIL:
        return notice.attachment_name, render_pdf(contract_pdf_page(version))
    page = notice_letter_page(version, recipient=notice.recipient, subject=notice.subject, body=notice.body,
                              notice_date=notice.notice_date)
    return f"Vertragsmitteilung_v{notice.contract_version}_Nr{notice.id}.pdf", render_pdf(page)


def handle_get(ctx: RequestContext) -> str:
    """Overview, one contract notice, or the printable EBL notice of one participant."""
    if len(ctx.segments) == 2 and ctx.segments[0] == "notices":
        return _notice_page(ctx, ctx.segments[1])
    if len(ctx.segments) == 2 and ctx.segments[0] == "ebl":
        notice = ebl_join_notice(ctx.db_path, ctx.segments[1])
        if notice is not None:
            return jinja_env.get_template("documents/ebl_join_print.html").render(
                notice=notice, printed_on=date.today(),
                title=f"Meldung Eintritt LEG – {notice.participant_name}",
            )
        return render_page("placeholders/coming_soon.html", ctx, "documents", title="Teilnehmer nicht gefunden",
                           description="Bitte die Meldung erneut aus der Liste öffnen.")
    return render_page("documents/list.html", ctx, "documents", notices=ebl_join_notices(ctx.db_path),
                       contract_notices=list_notices(ctx.db_path))
