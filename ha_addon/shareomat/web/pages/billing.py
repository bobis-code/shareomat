# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/billing.py

Purpose:
    "Abrechnungen" admin page: pick a period, check available meter data,
    preview a computed billing, save it as a draft, release it, or
    cancel it, and browse previously saved (immutable) billing runs.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Routes (relative to /billing, dispatched via ctx.segments):
        GET  /                       overview of all billing runs
        GET  /new                    period picker
        GET  /preview?period_start=&period_end=   live (recomputed) preview
        POST /save                   recompute once more and persist as a draft
        GET  /<id>                   saved, immutable run detail
        POST /<id>/release           draft -> released
        POST /<id>/cancel            draft|released -> cancelled
"""

from __future__ import annotations

from datetime import date

from shareomat.database.billing import (
    BillingWorkflowError,
    cancel_billing_run,
    compute_billing_preview,
    get_billing_run,
    list_billing_runs,
    release_billing_run,
    save_draft,
)
from shareomat.database.config_builder import IncompleteConfigError
from shareomat.web.navigation import url_for
from shareomat.web.rendering import FormError, Redirect, RequestContext, render_page
from shareomat.web.state import get_state


def _parse_period(ctx: RequestContext, source: dict[str, str]) -> tuple[date, date]:
    try:
        period_start = date.fromisoformat(source.get("period_start", ""))
        period_end = date.fromisoformat(source.get("period_end", ""))
    except ValueError:
        raise FormError("Bitte ein gültiges Start- und Enddatum angeben.")
    return period_start, period_end


def handle_get(ctx: RequestContext) -> str:
    if not ctx.segments:
        runs = list_billing_runs(ctx.db_path)
        return render_page("billing/list.html", ctx, "billing", runs=runs)

    if ctx.segments == ["new"]:
        return render_page("billing/new.html", ctx, "billing", today=date.today().isoformat())

    if ctx.segments == ["preview"]:
        period_start, period_end = _parse_period(ctx, ctx.query)
        try:
            preview = compute_billing_preview(ctx.db_path, ctx.runtime, period_start, period_end)
        except IncompleteConfigError as exc:
            return render_page(
                "placeholders/coming_soon.html", ctx, "billing",
                title="Einrichtung unvollständig", description=str(exc),
            )
        except BillingWorkflowError as exc:
            return render_page(
                "placeholders/coming_soon.html", ctx, "billing",
                title="Ungültiger Zeitraum", description=str(exc),
            )
        return render_page("billing/preview.html", ctx, "billing", preview=preview)

    # /billing/<id>
    try:
        run_id = int(ctx.segments[0])
    except ValueError:
        return render_page("placeholders/coming_soon.html", ctx, "billing",
                            title="Seite nicht gefunden", description="")
    try:
        detail = get_billing_run(ctx.db_path, run_id)
    except BillingWorkflowError:
        return render_page("placeholders/coming_soon.html", ctx, "billing",
                            title="Abrechnung nicht gefunden", description="")
    return render_page("billing/detail.html", ctx, "billing", detail=detail)


def handle_post(ctx: RequestContext) -> str | None:
    if ctx.segments == ["save"]:
        period_start, period_end = _parse_period(ctx, ctx.form)
        try:
            preview = compute_billing_preview(ctx.db_path, ctx.runtime, period_start, period_end)
        except (IncompleteConfigError, BillingWorkflowError) as exc:
            raise FormError(str(exc))
        run = save_draft(ctx.db_path, preview)
        get_state().set_flash(f"Abrechnung als Entwurf gespeichert (Version {run.version}).", ok=True)
        raise Redirect(url_for(ctx.ingress_path, "billing", suffix=f"/{run.id}"))

    if len(ctx.segments) == 2 and ctx.segments[1] == "release":
        run_id = int(ctx.segments[0])
        try:
            release_billing_run(ctx.db_path, run_id)
        except BillingWorkflowError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Abrechnung freigegeben.", ok=True)
        raise Redirect(url_for(ctx.ingress_path, "billing", suffix=f"/{run_id}"))

    if len(ctx.segments) == 2 and ctx.segments[1] == "cancel":
        run_id = int(ctx.segments[0])
        try:
            cancel_billing_run(ctx.db_path, run_id)
        except BillingWorkflowError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Abrechnung storniert.", ok=True)
        raise Redirect(url_for(ctx.ingress_path, "billing", suffix=f"/{run_id}"))

    raise FormError("Unbekannte Aktion.")
