# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/checklist.py

Purpose:
    "VERWALTUNG → Checkliste": one page that shows per participant what is
    done, what is missing and what waits for the grid operator, with the
    forms to do it right there — and the collective actions for the same
    administrative step for several participants (contract notice, consent
    with a common document, report to / confirmation by the grid operator).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    GET  /checklist                       overview (?f=filter &q=search &m=YYYY-MM &p=participant)
    GET  /checklist?batch=<kind>&v=<id>    selection form of a collective action (&t=<token>: preview)
    POST /checklist/batch                 step=preview: check and park the selection under a one-time token
                                          step=apply:   apply exactly the previewed selection (token used once)
    POST /checklist/review/<membership>   confirm a membership flagged by the schema-18 migration

    The per-participant forms post to the existing endpoints (participants,
    meters, contract) with return_to=checklist, so there is one set of
    business rules, uploads and permissions. Ticking a box in a collective
    action only selects; nothing is stored before "Übernehmen" after the
    preview, and a token can be applied once only (no double submit).
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import date
from urllib.parse import quote

from shareomat.core.paperless import PaperlessValueError, document_link, parse_number
from shareomat.database.audit import list_events
from shareomat.database.contract_versions import get_contract_version, list_contract_versions
from shareomat.database.memberships import MembershipError, confirm_review
from shareomat.database.participant_checklist import COLUMNS, STATE_LABELS, STATE_SYMBOLS, build_checklist
from shareomat.database.participant_contract import (
    ACCEPTANCE_METHODS,
    BATCH_KINDS,
    NOTICE_METHODS,
    ParticipantContractError,
    apply_batch,
    preview_batch,
)
from shareomat.security.context import current_principal
from shareomat.web.navigation import url_for
from shareomat.web.pages.meters import METER_ROLES
from shareomat.web.rendering import FormError, RequestContext, form_date, render_page
from shareomat.web.state import get_state

FILTERS = {"all": "Alle", "open": "Offen", "waiting": "Wartet auf EBL", "check": "Prüfen",
           "complete": "Vollständig", "departed": "Ausgetreten"}
_TOKEN_TTL_SECONDS = 15 * 60
_MAX_PENDING = 100
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()


# ── Private helpers ─────────────────────────────────────────────────────────


def _month(raw: str | None) -> date:
    try:
        year, month = (raw or "").split("-")
        return date(int(year), int(month), 1)
    except ValueError:
        return date.today().replace(day=1)


def _month_choices(selected: date) -> list[tuple[str, str, bool]]:
    today = date.today().replace(day=1)
    choices = []
    for offset in (-2, -1, 0, 1, 2):
        month_index = today.year * 12 + today.month - 1 + offset
        d = date(month_index // 12, month_index % 12 + 1, 1)
        choices.append((d.strftime("%Y-%m"), d.strftime("%m.%Y"), d == selected))
    return choices


def _matches(row, flt: str, query: str) -> bool:
    if query and query not in (row.participant.label or "").casefold() \
            and query not in row.participant.participant_id.casefold():
        return False
    return {
        "all": True,
        "open": not row.departed and not row.admin_complete,
        "waiting": not row.departed and row.waiting,
        "check": row.needs_check,
        "complete": not row.departed and row.admin_complete,
        "departed": row.departed,
    }.get(flt, True)


def _user_id() -> int | None:
    principal = current_principal()
    return principal.user_id if principal else None


def _recorded_by() -> str:
    principal = current_principal()
    return principal.username if principal else "system"


def _park(payload: dict) -> str:
    now = time.monotonic()
    token = secrets.token_urlsafe(18)
    with _pending_lock:
        for key in [k for k, v in _pending.items() if now - v["created"] > _TOKEN_TTL_SECONDS]:
            del _pending[key]
        while len(_pending) >= _MAX_PENDING:
            del _pending[min(_pending, key=lambda k: _pending[k]["created"])]
        _pending[token] = {**payload, "created": now, "user": _user_id()}
    return token


def _peek(token: str) -> dict | None:
    with _pending_lock:
        entry = _pending.get(token or "")
    if entry is None or time.monotonic() - entry["created"] > _TOKEN_TTL_SECONDS or entry["user"] != _user_id():
        return None
    return entry


def _take(token: str) -> dict | None:
    with _pending_lock:
        entry = _pending.pop(token or "", None)
    if entry is None or time.monotonic() - entry["created"] > _TOKEN_TTL_SECONDS or entry["user"] != _user_id():
        return None
    return entry


def _batch_values(ctx: RequestContext, kind: str) -> dict:
    """The common values of a collective action from the form, validated."""
    spec = BATCH_KINDS[kind]
    values: dict = {}
    day = form_date(ctx, "batch_date")
    if day is None:
        raise FormError(f"{spec['date_label']} ist erforderlich.")
    values["day"] = day
    if kind == "vnb_confirmed":
        start = form_date(ctx, "effective_start")
        if start is None:
            raise FormError("Der bestätigte Beginn (Monatserster) ist erforderlich.")
        values["effective_start"] = start
    if kind == "notice":
        method = (ctx.form.get("notice_method") or "").strip()
        if method not in NOTICE_METHODS:
            raise FormError("Bitte die Versandart wählen.")
        values["method"] = method
    values["evidence"] = (ctx.form.get("evidence") or "").strip()[:500]
    try:
        values["paperless_id"] = parse_number(ctx.form.get("paperless_id"), label="Paperless-Nr.")
        values["paperless_asn"] = parse_number(ctx.form.get("paperless_asn"), label="ASN")
    except PaperlessValueError as exc:
        raise FormError(str(exc))
    if kind == "consent":
        if not values["evidence"] and values["paperless_id"] is None:
            raise FormError("Für eine gesammelte Zustimmung ist ein gemeinsamer Nachweis nötig "
                            "(Beschreibung oder Paperless-Nr.).")
        if ctx.form.get("confirm_common_document") != "on":
            raise FormError("Bitte bestätigen, dass der gemeinsame Nachweis die ausdrückliche Zustimmung "
                            "jedes ausgewählten Teilnehmers belegt.")
    return values


def _selected_ids(ctx: RequestContext) -> list[int]:
    ids = []
    for key, value in ctx.form.items():
        if key.startswith("sel_") and value == "on":
            try:
                ids.append(int(key[4:]))
            except ValueError:
                continue
    return sorted(set(ids))


def _batch_candidates(checklist, kind: str, version_id: int | None) -> list:
    """Assignments the collective action of `kind` can apply to (one per running membership)."""
    if kind in ("notice", "consent"):
        for task in checklist.version_tasks:
            if task.kind == kind and task.version.id == version_id:
                return task.assignments
        return []
    result = []
    for row in checklist.rows:
        if row.departed or row.membership is None or not row.assignments:
            continue
        if kind == "vnb_notified" and row.membership.vnb_notified_at is None:
            result.append(max(row.assignments, key=lambda a: a.version_number or 0))
        if kind == "vnb_confirmed" and row.membership.vnb_confirmed_at is None:
            result.append(max(row.assignments, key=lambda a: a.version_number or 0))
    return result


def _joinable_versions(db_path) -> list:
    today = date.today()
    return sorted((v for v in list_contract_versions(db_path)
                   if v.status == "published" and v.valid_from is not None
                   and (v.valid_until is None or v.valid_until >= today)), key=lambda v: v.valid_from)


# ── Public interface ────────────────────────────────────────────────────────


def handle_get(ctx: RequestContext) -> str:
    """Render the checklist, a participant's detail panel, or a collective action."""
    month = _month(ctx.query.get("m"))
    checklist = build_checklist(ctx.db_path, month=month, swisseldex=get_state().swisseldex)
    flt = ctx.query.get("f") if ctx.query.get("f") in FILTERS else "all"
    query = (ctx.query.get("q") or "").strip().casefold()[:100]
    rows = [r for r in checklist.rows if _matches(r, flt, query)]

    selected = None
    selected_id = ctx.query.get("p") or ""
    if selected_id:
        selected = next((r for r in checklist.rows if r.participant.participant_id == selected_id), None)

    batch_kind = ctx.query.get("batch") if ctx.query.get("batch") in BATCH_KINDS else None
    batch_version = None
    candidates, preview, token = [], None, ""
    if batch_kind:
        try:
            version_id = int(ctx.query.get("v") or 0) or None
        except ValueError:
            version_id = None
        batch_version = get_contract_version(ctx.db_path, version_id) if version_id else None
        candidates = _batch_candidates(checklist, batch_kind, version_id)
        token = ctx.query.get("t") or ""
        parked = _peek(token) if token else None
        if parked and parked["kind"] == batch_kind:
            preview = parked
        else:
            token = ""

    runtime = ctx.runtime
    paperless_url = getattr(runtime, "paperless_url", "") if runtime else ""
    return render_page(
        "checklist/view.html", ctx, "checklist",
        checklist=checklist, rows=rows, columns=COLUMNS, filters=FILTERS, flt=flt, query=ctx.query.get("q") or "",
        month_choices=_month_choices(month), month_value=month.strftime("%Y-%m"),
        selected=selected, selected_id=selected_id,
        events=list_events(ctx.db_path, entity="participant", entity_id=selected_id, limit=40) if selected else [],
        joinable_versions=_joinable_versions(ctx.db_path), meter_roles=METER_ROLES, meter_role_labels=dict(METER_ROLES),
        acceptance_methods=ACCEPTANCE_METHODS, notice_methods=NOTICE_METHODS,
        batch_kind=batch_kind, batch_kinds=BATCH_KINDS, batch_version=batch_version, candidates=candidates,
        preview=preview, token=token, state_labels=STATE_LABELS, state_symbols=STATE_SYMBOLS,
        paperless_url=paperless_url, paperless_link=lambda number: document_link(paperless_url, number),
        today=date.today(), return_to_checklist=True,
    )


def handle_post(ctx: RequestContext) -> str | None:
    """Collective actions (preview, then apply once) and the review confirmation."""
    base = url_for(ctx.ingress_path, "checklist")
    if len(ctx.segments) == 2 and ctx.segments[0] == "review":
        try:
            membership = confirm_review(ctx.db_path, int(ctx.segments[1]))
        except (ValueError, MembershipError) as exc:
            raise FormError(str(exc))
        get_state().set_flash(f"Mitgliedschaft von {membership.participant_id} geprüft — Abrechnung wieder möglich.",
                              ok=True)
        return f"{base}?p={quote(membership.participant_id, safe='')}"

    if ctx.segments != ["batch"]:
        raise FormError("Unbekannte Aktion.")
    kind = ctx.form.get("kind") or ""
    if kind not in BATCH_KINDS:
        raise FormError("Unbekannte Sammelaktion.")
    try:
        version_id = int(ctx.form.get("version_id") or 0) or None
    except ValueError:
        version_id = None
    back = f"{base}?batch={kind}" + (f"&v={version_id}" if version_id else "")

    if ctx.form.get("step") == "apply":
        parked = _take(ctx.form.get("token") or "")
        if parked is None or parked["kind"] != kind:
            raise FormError("Die Vorschau ist abgelaufen oder wurde bereits übernommen — bitte neu auswählen.")
        try:
            result = apply_batch(ctx.db_path, kind, parked["assignment_ids"], recorded_by=_recorded_by(),
                                 **parked["values"])
        except ParticipantContractError as exc:
            raise FormError(str(exc))
        message = f"{BATCH_KINDS[kind]['title']}: für {len(result.done)} Teilnehmer gespeichert (Sammel-Nr. {result.batch_id})."
        if result.skipped:
            message += " Nicht übernommen: " + "; ".join(f"{pid} ({why})" for pid, why in result.skipped) + "."
        get_state().set_flash(message, ok=True)
        return base

    ids = _selected_ids(ctx)
    if not ids:
        raise FormError("Bitte mindestens einen Teilnehmer auswählen.")
    values = _batch_values(ctx, kind)
    try:
        checked = preview_batch(ctx.db_path, kind, ids, **values)
    except ParticipantContractError as exc:
        raise FormError(str(exc))
    token = _park({"kind": kind, "version_id": version_id, "assignment_ids": [a for a, _ in checked.ok],
                   "values": values, "ok": checked.ok, "skipped": checked.skipped})
    return f"{back}&t={token}"
