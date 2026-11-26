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
                                          kind=notice:  step=send sends the e-mails / creates the letters
                                          (from the form or after the preview), step=test sends one
                                          e-mail of the preview to an address of the operator's choice
    GET  /checklist/notice-pdf?t=&a=      PDF of a previewed notice (served by the web server itself)
    POST /checklist/notice/<id>/resolve   e-mail with unknown outcome: found in "Gesendet" or not
    POST /checklist/review/<membership>   confirm a membership flagged by the schema-18 migration

    The per-participant forms post to the existing endpoints (participants,
    meters, contract) with return_to=checklist, so there is one set of
    business rules, uploads and permissions. Ticking a box in a collective
    action only selects; nothing is stored before "Übernehmen" after the
    preview, and a token can be applied once only (no double submit).

    The contract notice is different: it is not just recorded, Shareomat
    produces it (shareomat.core.delivery.notice_mail). "Vorschau" shows each
    e-mail with the contract as PDF attachment (or the letter); "Senden"
    sends the e-mails or creates the letters, files them under
    "Dokumente → Meldungen & Mitteilungen" and only then records the notice.
"""

from __future__ import annotations

import secrets
import threading
import time
from datetime import date
from urllib.parse import quote

from shareomat.config import SmtpConfig, smtp_problems
from shareomat.core.delivery.mail import MailError, send_mail, valid_address
from shareomat.core.delivery.notice_mail import NoticeMailError, build_drafts, send_notices, test_mail
from shareomat.core.delivery.pdf import PdfError, pdf_unavailable_reason, render_pdf
from shareomat.core.paperless import PaperlessValueError, document_link, parse_number
from shareomat.database.audit import list_events, log_event
from shareomat.database.contract_notices import ContractNoticeError, local_today, resolve_unclear
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
from shareomat.models.contract_notice import NOTICE_METHOD_EMAIL, SENDABLE_NOTICE_METHODS
from shareomat.security.context import current_principal
from shareomat.web.navigation import url_for
from shareomat.web.pages.documents import contract_pdf_page, notice_letter_page
from shareomat.web.pages.email_templates import take_test_slot
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


def _smtp() -> SmtpConfig | None:
    runtime = get_state().runtime
    return runtime.smtp if runtime is not None else None


def _mail_problems() -> list[str]:
    """Why e-mails can not go out right now (mailbox not set up, no PDF library); [] when ready."""
    smtp = _smtp()
    problems = smtp_problems(smtp) if smtp is not None else ["Keine Laufzeitkonfiguration geladen."]
    reason = pdf_unavailable_reason()
    return problems + ([reason] if reason else [])


def _notice_choice(ctx: RequestContext) -> tuple[str, date]:
    """Versandart and Versanddatum of a contract notice from the form; an e-mail always goes out today."""
    method = (ctx.form.get("notice_method") or "").strip()
    if method not in SENDABLE_NOTICE_METHODS:
        raise FormError("Bitte die Versandart wählen.")
    day = form_date(ctx, "batch_date")
    if day is None:
        raise FormError("Das Versanddatum ist erforderlich.")
    if method == NOTICE_METHOD_EMAIL and day != local_today():
        raise FormError("Eine E-Mail geht sofort hinaus: Bei E-Mail ist das Versanddatum heute.")
    return method, day


def _notice_version(db_path, version_id: int | None):
    version = get_contract_version(db_path, version_id) if version_id else None
    if version is None:
        raise FormError("Vertragsversion nicht gefunden — bitte neu auswählen.")
    return version


def _notice_preview(db_path, parked: dict) -> dict:
    """What the preview page shows: per participant the e-mail (or letter), or why it can not go out."""
    view = {"method": parked["method"], "method_label": SENDABLE_NOTICE_METHODS[parked["method"]],
            "day": parked["day"], "drafts": [], "error": "", "problems": []}
    try:
        view["drafts"] = build_drafts(db_path, parked["version_id"], parked["assignment_ids"],
                                      method=parked["method"], notice_date=parked["day"])
    except NoticeMailError as exc:
        view["error"] = str(exc)
    view["ready"] = [d for d in view["drafts"] if not d.refusal]
    if parked["method"] == NOTICE_METHOD_EMAIL:
        view["problems"] = _mail_problems()
        smtp = _smtp()
        view["suggestions"] = [smtp.from_address] if smtp is not None and smtp.from_address else []
    return view


def _send_notices(ctx: RequestContext, version_id: int | None) -> str:
    """step=send: from the form directly, or exactly the previewed selection (token)."""
    token = ctx.form.get("token") or ""
    if token:
        parked = _take(token)
        if parked is None or parked["kind"] != "notice":
            raise FormError("Die Vorschau ist abgelaufen oder wurde bereits gesendet — bitte neu auswählen.")
        ids, method, day, version_id = parked["assignment_ids"], parked["method"], parked["day"], parked["version_id"]
        if method == NOTICE_METHOD_EMAIL:
            day = local_today()
    else:
        ids = _selected_ids(ctx)
        if not ids:
            raise FormError("Bitte mindestens einen Teilnehmer auswählen.")
        method, day = _notice_choice(ctx)
    version = _notice_version(ctx.db_path, version_id)
    if method == NOTICE_METHOD_EMAIL:
        problems = _mail_problems()
        if problems:
            raise FormError("E-Mail-Versand nicht möglich: " + problems[0])
    try:
        outcome = send_notices(ctx.db_path, _smtp(), version.id, ids, method=method, notice_date=day,
                               recorded_by=_recorded_by(), contract_page_html=contract_pdf_page(version))
    except NoticeMailError as exc:
        raise FormError(str(exc))
    verb = "gesendet" if method == NOTICE_METHOD_EMAIL else "erstellt"
    message = f"Vertragsmitteilung v{version.version}: {len(outcome.done)} {verb} und eingetragen."
    if outcome.failed:
        message += " Nicht " + verb + ": " + "; ".join(f"{pid} ({why})" for pid, why in outcome.failed) + "."
    if outcome.notes:
        message += " Gesendet, aber nicht eingetragen: " + "; ".join(f"{pid} ({why})" for pid, why in outcome.notes) + "."
    get_state().set_flash(message, ok=not outcome.failed and not outcome.notes)
    return url_for(ctx.ingress_path, "documents") + "#mitteilungen"


def _send_notice_test(ctx: RequestContext, back: str) -> str:
    """step=test: one e-mail of the preview, marked TEST, to an address of the operator's choice; nothing recorded."""
    token = ctx.form.get("token") or ""
    parked = _peek(token)
    if parked is None or parked["kind"] != "notice":
        raise FormError("Die Vorschau ist abgelaufen — bitte neu auswählen.")
    back = f"{back}&t={token}"
    if parked["method"] != NOTICE_METHOD_EMAIL:
        raise FormError("Ein Test-Versand gibt es nur bei der Versandart E-Mail.")
    problems = _mail_problems()
    if problems:
        raise FormError("Test nicht möglich: " + problems[0])
    to = (ctx.form.get("to_address") or "").strip()
    if not valid_address(to):
        raise FormError("Bitte eine gültige E-Mail-Adresse eintragen (z. B. name@example.ch).")
    preview = _notice_preview(ctx.db_path, parked)
    if not preview["ready"]:
        raise FormError("In der Vorschau ist keine E-Mail, die gesendet werden könnte.")
    draft = preview["ready"][0]
    version = _notice_version(ctx.db_path, parked["version_id"])
    try:
        pdf = render_pdf(contract_pdf_page(version))
    except PdfError as exc:
        raise FormError(f"Test nicht gesendet, PDF fehlt: {exc}")
    if not take_test_slot():
        raise FormError("Zu viele Test-E-Mails in der letzten Stunde. Bitte später wieder.")
    label = f"Vertragsmitteilung v{version.version} (wie an {draft.participant_id})"
    try:
        send_mail(_smtp(), test_mail(draft, pdf, to))
    except MailError as exc:
        log_event(ctx.db_path, "email", "test", "failed", f"{label} an {to}: {exc}")
        get_state().set_flash(f"Test-E-Mail nicht gesendet: {exc}", ok=False)
        return back
    log_event(ctx.db_path, "email", "test", "sent", f"{label} an {to}")
    get_state().set_flash(f"Test-E-Mail an {to} gesendet: {label}. Nichts eingetragen.", ok=True)
    return back


def _notice_post(ctx: RequestContext, version_id: int | None, back: str) -> str:
    """Contract notice: preview (park the selection), send, or a test e-mail of the preview."""
    step = ctx.form.get("step")
    if step == "send":
        return _send_notices(ctx, version_id)
    if step == "test":
        return _send_notice_test(ctx, back)
    ids = _selected_ids(ctx)
    if not ids:
        raise FormError("Bitte mindestens einen Teilnehmer auswählen.")
    method, day = _notice_choice(ctx)
    version = _notice_version(ctx.db_path, version_id)
    token = _park({"kind": "notice", "version_id": version.id, "assignment_ids": ids, "method": method, "day": day})
    return f"{back}&t={token}"


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
            preview = _notice_preview(ctx.db_path, parked) if batch_kind == "notice" else parked
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
        send_methods=SENDABLE_NOTICE_METHODS, local_today=local_today(),
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

    if len(ctx.segments) == 3 and ctx.segments[0] == "notice" and ctx.segments[2] == "resolve":
        found = ctx.form.get("found") == "1"
        try:
            note = resolve_unclear(ctx.db_path, int(ctx.segments[1]), found_in_sent=found, recorded_by=_recorded_by())
        except (ValueError, ContractNoticeError) as exc:
            raise FormError(str(exc))
        if found:
            get_state().set_flash("Als gesendet eingetragen." + (f" Mitteilung nicht eingetragen: {note}" if note else ""),
                                  ok=not note)
        else:
            get_state().set_flash("Als nicht gesendet markiert. Die Mitteilung kann jetzt neu gesendet werden.", ok=True)
        return url_for(ctx.ingress_path, "documents", suffix=f"/notices/{int(ctx.segments[1])}")

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

    if kind == "notice":
        return _notice_post(ctx, version_id, back)

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


def notice_draft_pdf(db_path, query: dict) -> tuple[str, bytes]:
    """(file name, PDF) of one previewed notice: the e-mail's contract attachment, or the letter.

    GET /checklist/notice-pdf?t=<token>&a=<assignment id>; only for the
    user's own, still valid preview and only for a participant in it.
    """
    parked = _peek(query.get("t") or "")
    if parked is None or parked["kind"] != "notice":
        raise FormError("Die Vorschau ist abgelaufen — bitte neu auswählen.")
    version = _notice_version(db_path, parked["version_id"])
    if parked["method"] == NOTICE_METHOD_EMAIL:
        return f"Gesellschaftsvertrag_v{version.version}.pdf", render_pdf(contract_pdf_page(version))
    raw = query.get("a") or ""
    assignment_id = int(raw) if raw.isdigit() else None
    draft = next((d for d in _notice_preview(db_path, parked)["ready"] if d.assignment_id == assignment_id), None)
    if draft is None:
        raise FormError("Dieses Schreiben ist nicht in der Vorschau.")
    page = notice_letter_page(version, recipient=draft.to, subject=draft.subject, body=draft.body,
                              notice_date=draft.notice_date)
    return f"Vertragsmitteilung_v{version.version}_Entwurf.pdf", render_pdf(page)
