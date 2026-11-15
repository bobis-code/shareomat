# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/settings.py

Purpose:
    "Einstellungen" admin page: basic processing rules (unknown-meter
    policy, archiving), the "Sicherung" card (create and download backups,
    see shareomat.database.backup) plus a read-only view of the technical
    runtime configuration (managed via Docker/Home-Assistant add-on
    options, not editable here).

    POST /settings                 save processing settings
    POST /settings/backup          create a backup (password if the last entry is older than 10 min)
    POST /settings/backup/confirm  only confirm the password, so existing backups can be downloaded
    GET  /settings/backup/<name>   download (served by the web server itself)

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import replace

from shareomat.database.audit import log_event
from shareomat.database.backup import BackupError, backup_dir, create_backup, list_backups
from shareomat.database.settings import get_operation_settings, save_operation_settings
from shareomat.leg_const import (
    METER_DATA_SOURCE_EMAIL_CSV,
    METER_DATA_SOURCE_SDAT_LEG,
    UNKNOWN_METER_POLICY_FAIL,
    UNKNOWN_METER_POLICY_SKIP,
)
from shareomat.web.rendering import FormError, RequestContext, form_bool, render_page
from shareomat.web.state import get_state

UNKNOWN_METER_POLICIES = [
    (UNKNOWN_METER_POLICY_FAIL, "Lauf abbrechen (empfohlen)"),
    (UNKNOWN_METER_POLICY_SKIP, "Unbekannte Messwerte überspringen"),
]

METER_DATA_SOURCES = [
    (METER_DATA_SOURCE_EMAIL_CSV, "E-Mail / CSV (aktuell)"),
    (METER_DATA_SOURCE_SDAT_LEG, "SDAT (EBL/Swisseldex) — offizielle Zuteilung des Netzbetreibers"),
]


def _documents_dir(ctx: RequestContext):
    from shareomat.web.pages.contract import documents_dir
    return documents_dir(ctx.db_path)


def handle_get(ctx: RequestContext) -> str:
    """Render processing settings, the backup card and a read-only technical-config summary."""
    from shareomat.security.context import current_principal
    from shareomat.web.login import recent_auth_required

    settings = get_operation_settings(ctx.db_path)
    folder = backup_dir(ctx.runtime) if ctx.runtime else None
    return render_page(
        "settings/edit.html", ctx, "settings",
        settings=settings, runtime=ctx.runtime, policies=UNKNOWN_METER_POLICIES,
        meter_data_sources=METER_DATA_SOURCES,
        backup_folder=folder, backups=list_backups(folder) if folder else [],
        backup_password_needed=recent_auth_required(current_principal()),
    )


def _create_backup(ctx: RequestContext) -> None:
    if ctx.runtime is None:
        raise FormError("Keine Laufzeitkonfiguration: Sicherungsordner unbekannt.")
    try:
        info = create_backup(ctx.db_path, backup_dir(ctx.runtime), documents_dir=_documents_dir(ctx))
    except BackupError as exc:
        raise FormError(f"Sicherung nicht erstellt: {exc}")
    except OSError as exc:
        raise FormError(f"Sicherung nicht erstellt: Ordner nicht beschreibbar ({exc.strerror or exc}).")
    log_event(ctx.db_path, "backup", info.name, "created", f"{info.size} Bytes")
    get_state().set_flash(f"Sicherung erstellt und geprüft: {info.name}.", ok=True)


def handle_post(ctx: RequestContext) -> str | None:
    """Save processing settings, or create a backup / confirm the password for downloads."""
    if ctx.segments == ["backup"]:
        _create_backup(ctx)
        return None
    if ctx.segments == ["backup", "confirm"]:
        # The password itself is checked by the web server before this runs.
        get_state().set_flash("Passwort bestätigt. Sicherungen können jetzt 10 Minuten lang heruntergeladen werden.", ok=True)
        return None
    if ctx.segments:
        raise FormError("Unbekannte Aktion.")
    current = get_operation_settings(ctx.db_path)
    policy = (ctx.form.get("unknown_meter_policy") or "").strip()
    if policy not in {UNKNOWN_METER_POLICY_FAIL, UNKNOWN_METER_POLICY_SKIP}:
        raise FormError("Ungültige Auswahl bei 'Umgang mit unbekannten Messpunkten'.")

    meter_data_source = (ctx.form.get("meter_data_source") or "").strip()
    if meter_data_source not in {METER_DATA_SOURCE_EMAIL_CSV, METER_DATA_SOURCE_SDAT_LEG}:
        raise FormError("Ungültige Auswahl bei 'Messdaten-Quelle'.")

    updated = replace(
        current,
        unknown_meter_policy=policy,
        archive_processed=form_bool(ctx, "archive_processed"),
        meter_data_source=meter_data_source,
    )
    save_operation_settings(ctx.db_path, updated)
    get_state().set_flash("Einstellungen gespeichert.", ok=True)
    return None
