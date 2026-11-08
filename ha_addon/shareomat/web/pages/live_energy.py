# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/live_energy.py

Purpose:
    "Betrieb -> Live-Energie": overview of all official meters with their
    live source, and "Live-Messquelle zuordnen" (ADR-0007, 5.1).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Administrators only (normal login and permission gate of the admin
    server). A single measurement of a meter is shown as soon as it is
    valid; a partial LEG sum is shown here only and marked as incomplete.
"""

from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from shareomat.database.live_sources import (
    LiveSourceError,
    activate_live_source,
    clear_identity_proof,
    create_live_source,
    get_live_source,
    list_live_sources,
    record_identity_proof,
    retire_live_source,
    update_live_source,
)
from shareomat.database.meters import get_meter
from shareomat.leg_const import METER_ROLE_CONSUMER, METER_ROLE_GRID
from shareomat.models.live_source import (
    ADAPTER_EMSOMAT_LOCAL,
    ADAPTER_GATEWAY_MQTT,
    ADAPTER_HA_MQTT,
    ADAPTER_SOURCE_TYPES,
    ADAPTER_WAN_SPARKPLUG,
    MODE_IMPORT_EXPORT,
    MODE_IMPORT_ONLY,
    MODE_SIGNED,
    SIGN_POSITIVE_IS_EXPORT,
    SIGN_POSITIVE_IS_IMPORT,
    STATE_RETIRED,
    STATE_TESTING,
)
from shareomat.web.rendering import FormError, RequestContext, form_required, render_page
from shareomat.web.state import get_state

ROUTE = "live_energy"

# (adapter, label) - what the administrator chooses, without technical detail.
SOURCE_CHOICES = [
    (ADAPTER_EMSOMAT_LOCAL, "Emsomat / Sparkplug"),
    (ADAPTER_HA_MQTT, "Home Assistant / HA-Sensoren"),
    (ADAPTER_GATEWAY_MQTT, "Externe Quelle / Gateway"),
    (ADAPTER_WAN_SPARKPLUG, "Externe Quelle / anderer Standort (WAN-Sparkplug)"),
]
SOURCE_LABELS = dict(SOURCE_CHOICES)
QUALITY_LABELS = {"ok": "gültig", "missing": "fehlt", "invalid": "ungültig", "stale": "veraltet", "conflict": "widersprüchlich"}
COMM_LABELS = {"online": "verbunden", "offline": "getrennt", "nie_empfangen": "nie empfangen"}
STATUS_LABELS = {"vollstaendig": "Vollständig", "unsicher": "Unvollständig", "keine_daten": "Keine Live-Daten"}
STATE_LABELS = {"testing": "geplant / in Prüfung", "active": "aktiv", "retired": "abgelöst"}
LIVE_STATUS_LABELS = {"none": "Keine Live-Messquelle", "planned": "Live-Messquelle geplant", "active": "Live-Messquelle aktiv"}
MODE_LABELS = {
    MODE_IMPORT_EXPORT: "Bezug und Einspeisung getrennt",
    MODE_SIGNED: "Ein Wert mit Vorzeichen",
    MODE_IMPORT_ONLY: "Nur Bezug (Einspeisung ausgeschlossen)",
}
EVIDENCE_LABELS = {
    "C": "C: nur Übertragung (z. B. Home-Assistant-Zeitstempel)",
    "B": "B: Erfassungsnachweis mit Zähler",
    "A": "A: Zeitstempel des Messgeräts mit Zähler",
}
_DEFAULT_MAX_POWER_W = 17250     # 3 x 25 A x 230 V


def _service():
    return get_state().live_service


def _local(value: datetime | None) -> str:
    if value is None:
        return "–"
    zone = ZoneInfo(os.environ.get("SHAREOMAT_TZ", "Europe/Zurich"))
    return value.astimezone(zone).strftime("%d.%m.%Y %H:%M:%S")


def _watts(value: float | None) -> str:
    return "–" if value is None else f"{value:,.0f} W".replace(",", "'")


def _source_view(source, reading, now: datetime) -> dict:
    age = None
    if reading is not None and reading.measured_at is not None:
        age = max(0, int((now - reading.measured_at).total_seconds()))
    return {
        "id": source.id,
        "type_label": SOURCE_LABELS.get(source.adapter, source.adapter),
        "key": source.source_key.split(":", 1)[-1],
        "state": source.state,
        "state_label": STATE_LABELS[source.state],
        "mode_label": MODE_LABELS.get(source.measurement_mode, source.measurement_mode),
        "evidence": source.freshness_evidence,
        "identity_verified": source.identity_verified,
        "identity_at": _local(source.identity_verified_at),
        "identity_note": source.identity_note,
        "max_power_w": _watts(source.max_power_w),
        "quality": reading.quality if reading else "missing",
        "quality_label": QUALITY_LABELS.get(reading.quality, reading.quality) if reading else "fehlt",
        "reason": reading.reason if reading else "",
        "communication": COMM_LABELS.get(reading.communication, "") if reading else "",
        "import_w": _watts(reading.import_w) if reading else "–",
        "export_w": _watts(reading.export_w) if reading else "–",
        "measured_at": _local(reading.measured_at) if reading else "–",
        "age_s": age,
        "time_origin": (reading.time_origin or "–") if reading else "–",
        "last_message_at": _local(reading.last_message_at) if reading else "–",
        "last_valid_at": _local(reading.last_valid_at) if reading else "–",
        "last_valid_import_w": _watts(reading.last_valid_import_w) if reading else "–",
        "last_valid_export_w": _watts(reading.last_valid_export_w) if reading else "–",
        "dropped": reading.dropped if reading else 0,
    }


def _overview(ctx: RequestContext) -> str:
    service = _service()
    if service is None:
        return render_page("live_energy/overview.html", ctx, ROUTE, available=False)
    now = service.now()
    status = service.leg_status()
    rows = []
    for row in service.meter_rows(now=now):
        rows.append({
            "meter_id": row.meter_id,
            "label": row.label,
            "participant_id": row.participant_id,
            "in_leg": row.in_leg,
            "excluded_reason": row.excluded_reason,
            "unconfirmed": row.unconfirmed,
            "live_status": row.live_status,
            "live_status_label": LIVE_STATUS_LABELS[row.live_status],
            "active": _source_view(row.active_source, row.active_reading, now) if row.active_source else None,
            "planned": [_source_view(source, reading, now) for source, reading in row.planned],
            "open_reasons": row.open_reasons,
        })
    own = service.own_reading(now=now)
    return render_page(
        "live_energy/overview.html", ctx, ROUTE,
        available=True,
        status=status,
        status_label=STATUS_LABELS[status.status],
        partial_surplus=_watts(status.partial_surplus_w),
        surplus=_watts(status.surplus_w),
        updated_at=_local(status.updated_at),
        oldest_measured_at=_local(status.oldest_measured_at),
        coverage_pct=round(status.coverage * 100),
        own=own,
        own_view={
            "quality_label": QUALITY_LABELS.get(own.quality, own.quality),
            "communication": COMM_LABELS.get(own.communication, ""),
            "import_w": _watts(own.import_w),
            "export_w": _watts(own.export_w),
            "measured_at": _local(own.measured_at),
        },
        rows=rows,
        params=service.params,
        events=[{"at": _local(e["occurred_at"]), "detail": e["detail"]} for e in service.recent_events()],
        unknown_pairs=service.unknown_wan_pairs(),
    )


def _configured_edge_node() -> str:
    runtime = get_state().runtime
    return runtime.sparkplug.emsomat_edge_node_id if runtime is not None else ""


def _assign_form(ctx: RequestContext, meter_id: str) -> str:
    meter = get_meter(ctx.db_path, meter_id)
    if meter is None or meter.role == METER_ROLE_GRID:
        return render_page("placeholders/coming_soon.html", ctx, ROUTE, title="Messpunkt nicht gefunden", description="")
    adapter = ctx.query.get("adapter", "")
    if adapter not in ADAPTER_SOURCE_TYPES:
        adapter = ""
    service = _service()
    return render_page(
        "live_energy/assign.html", ctx, ROUTE,
        meter=meter,
        adapter=adapter,
        adapter_label=SOURCE_LABELS.get(adapter, ""),
        source_choices=SOURCE_CHOICES,
        can_be_import_only=meter.role == METER_ROLE_CONSUMER,
        configured_edge_node=_configured_edge_node(),
        evidence_labels=EVIDENCE_LABELS,
        default_max_power=_DEFAULT_MAX_POWER_W,
        unknown_pairs=service.unknown_wan_pairs() if service else (),
        existing=[s for s in list_live_sources(ctx.db_path) if s.meter_id == meter_id],
        state_labels=STATE_LABELS,
    )


def _source_or_error(ctx: RequestContext, raw_id: str):
    try:
        source_id = int(raw_id)
    except (TypeError, ValueError):
        raise FormError("Ungültige Live-Messquelle.") from None
    source = get_live_source(ctx.db_path, source_id)
    if source is None:
        raise FormError("Die Live-Messquelle wurde nicht gefunden.")
    return source


def _edit_form(ctx: RequestContext, raw_id: str) -> str:
    try:
        source_id = int(raw_id)
    except (TypeError, ValueError):
        source_id = -1
    source = get_live_source(ctx.db_path, source_id)
    if source is None or source.state == STATE_RETIRED:
        return render_page("placeholders/coming_soon.html", ctx, ROUTE, title="Live-Messquelle nicht gefunden", description="")
    meter = get_meter(ctx.db_path, source.meter_id)
    return render_page(
        "live_energy/edit.html", ctx, ROUTE,
        source=source,
        meter=meter,
        type_label=SOURCE_LABELS.get(source.adapter, source.adapter),
        can_be_import_only=meter is not None and meter.role == METER_ROLE_CONSUMER,
        can_be_signed=source.adapter in (ADAPTER_HA_MQTT, ADAPTER_GATEWAY_MQTT),
        evidence_labels=EVIDENCE_LABELS,
        mode_labels=MODE_LABELS,
    )


def handle_get(ctx: RequestContext) -> str:
    if not ctx.segments:
        return _overview(ctx)
    if len(ctx.segments) == 2 and ctx.segments[1] == "assign":
        return _assign_form(ctx, ctx.segments[0])
    if len(ctx.segments) == 3 and ctx.segments[0] == "sources" and ctx.segments[2] == "edit":
        return _edit_form(ctx, ctx.segments[1])
    return render_page("placeholders/coming_soon.html", ctx, ROUTE, title="Seite nicht gefunden", description="")


def _reload() -> None:
    service = _service()
    if service is not None:
        service.reload_masterdata()


def _power_field(ctx: RequestContext) -> str:
    return form_required(ctx, "max_power_w", label="Maximale Leistung")


def _ha_config(ctx: RequestContext) -> dict:
    return {key: ctx.form.get(key, "") for key in ("import_entity", "export_entity", "signed_entity", "evidence_entity")}


def _handle_assign(ctx: RequestContext, meter_id: str) -> None:
    adapter = form_required(ctx, "adapter", label="Art der Quelle")
    edge_node_id = ctx.form.get("edge_node_id", "")
    configured = _configured_edge_node()
    if adapter == ADAPTER_EMSOMAT_LOCAL:
        # Shareomat listens to this one node only; anything else could never deliver.
        if not configured:
            raise FormError("In der Konfiguration ist kein lokaler Emsomat-Knoten eingetragen.")
        edge_node_id = configured
    try:
        create_live_source(
            ctx.db_path,
            meter_id=meter_id,
            adapter=adapter,
            edge_node_id=edge_node_id,
            device_id=ctx.form.get("device_id", ""),
            measurement_mode=ctx.form.get("measurement_mode", MODE_IMPORT_EXPORT),
            sign_convention=ctx.form.get("sign_convention") or None,
            no_export_note=ctx.form.get("no_export_note", ""),
            adapter_config=_ha_config(ctx) if adapter == ADAPTER_HA_MQTT else None,
            max_power_w=_power_field(ctx),
            freshness_evidence=ctx.form.get("freshness_evidence", "C"),
        )
    except LiveSourceError as exc:
        raise FormError(str(exc)) from exc
    _reload()
    get_state().set_flash(f"Live-Messquelle für '{meter_id}' als geplant eingetragen. Sie zählt noch nicht zur Bilanz.", ok=True)


def _handle_source_action(ctx: RequestContext, raw_id: str, action: str) -> None:
    source = _source_or_error(ctx, raw_id)
    state = get_state()
    try:
        if action == "edit":
            update_live_source(
                ctx.db_path, source.id,
                max_power_w=_power_field(ctx),
                freshness_evidence=ctx.form.get("freshness_evidence", "C"),
                measurement_mode=ctx.form.get("measurement_mode", MODE_IMPORT_EXPORT),
                sign_convention=ctx.form.get("sign_convention") or None,
                no_export_note=ctx.form.get("no_export_note", ""),
            )
            state.set_flash("Live-Messquelle gespeichert.", ok=True)
        elif action == "activate":
            service = _service()
            if service is None:
                raise FormError("Die Live-Ebene läuft nicht, eine Prüfung der Quelle ist nicht möglich.")
            allowed, reason = service.can_activate(source.id)
            if not allowed:
                raise FormError(f"Aktivieren nicht möglich. {reason}")
            replaced = activate_live_source(ctx.db_path, source.id)
            suffix = " Die bisherige aktive Quelle wurde abgelöst." if replaced else ""
            state.set_flash(f"Live-Messquelle aktiviert.{suffix}", ok=True)
        elif action == "retire":
            retire_live_source(ctx.db_path, source.id)
            state.set_flash("Live-Messquelle deaktiviert. Ihre Werte werden nicht mehr verwendet.", ok=True)
        elif action == "identity":
            record_identity_proof(ctx.db_path, source.id, note=ctx.form.get("identity_note", ""))
            state.set_flash("Nachweis der Quellenidentität eingetragen.", ok=True)
        elif action == "identity-clear":
            clear_identity_proof(ctx.db_path, source.id)
            state.set_flash("Nachweis der Quellenidentität entfernt.", ok=True)
        else:
            raise FormError("Unbekannte Aktion.")
    except LiveSourceError as exc:
        raise FormError(str(exc)) from exc
    _reload()


def handle_post(ctx: RequestContext) -> str | None:
    if len(ctx.segments) == 2 and ctx.segments[1] == "assign":
        _handle_assign(ctx, ctx.segments[0])
        return None
    if len(ctx.segments) == 3 and ctx.segments[0] == "sources":
        _handle_source_action(ctx, ctx.segments[1], ctx.segments[2])
        return None
    raise FormError("Unbekannte Aktion.")


__all__ = ["handle_get", "handle_post", "STATE_TESTING", "SIGN_POSITIVE_IS_IMPORT", "SIGN_POSITIVE_IS_EXPORT"]
