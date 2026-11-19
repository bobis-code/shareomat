# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/documents.py

Purpose:
    "Dokumente → EBL-Meldungen": the notice to the grid operator that a new
    participant joins the LEG (fields of EBL's participation form), per
    participant, with what is still missing and a printable page.

    GET /documents                      overview of all active participants
    GET /documents/ebl/<participant>    printable notice (standalone page, no menu)

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Read-only, nothing is stored. The print is Shareomat's own neutral layout
    of the form's content, never EBL's logo or letterhead (see
    shareomat.database.participant_documents).
"""

from __future__ import annotations

from datetime import date

from shareomat.database.participant_documents import ebl_join_notice, ebl_join_notices
from shareomat.web.rendering import RequestContext, _env as jinja_env, render_page


def handle_get(ctx: RequestContext) -> str:
    """Overview, or the printable notice of one participant."""
    if len(ctx.segments) == 2 and ctx.segments[0] == "ebl":
        notice = ebl_join_notice(ctx.db_path, ctx.segments[1])
        if notice is not None:
            return jinja_env.get_template("documents/ebl_join_print.html").render(
                notice=notice, printed_on=date.today(),
                title=f"Meldung Eintritt LEG – {notice.participant_name}",
            )
        return render_page("placeholders/coming_soon.html", ctx, "documents", title="Teilnehmer nicht gefunden",
                           description="Bitte die Meldung erneut aus der Liste öffnen.")
    return render_page("documents/list.html", ctx, "documents", notices=ebl_join_notices(ctx.db_path))
