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
    POST /settings/raw_backup      raw backup of the Swisseldex originals (password again, audit log);
                                   switching to "required" runs the storage test first
    POST /settings/raw_backup/test test the chosen storage with one small file (it stays there)
    GET  /settings/backup/<name>   download (served by the web server itself)

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import replace

from shareomat.core.collector.raw_backup import (
    DEFAULT_SUBDIR,
    CheckStep,
    RawBackupError,
    StorageCheck,
    check_storage,
    network_mounts,
    resolve_target,
    validate_subdir,
)
from shareomat.database.audit import log_event
from shareomat.database.backup import BackupError, backup_dir, create_backup, list_backups
from shareomat.database.settings import get_operation_settings, save_operation_settings
from shareomat.leg_const import RAW_BACKUP_MODE_OFF, RAW_BACKUP_MODE_REQUIRED
from shareomat.web.rendering import FormError, RequestContext, render_page
from shareomat.web.state import get_state


RAW_BACKUP_MODES = [
    (RAW_BACKUP_MODE_OFF, "Aus (Übergang) – Originale nur im lokalen Archiv"),
    (RAW_BACKUP_MODE_REQUIRED, "Pflicht – jede Datei erst nach geprüfter Sicherung verarbeiten"),
]


def _raw_backup_view(settings) -> dict:
    """Mode, choosable storages and the state of the chosen one (mounts an automount to check it)."""
    view = {"mode": settings.raw_backup_mode, "mount": settings.raw_backup_mount,
            "subdir": settings.raw_backup_subdir or DEFAULT_SUBDIR, "mounts": network_mounts(),
            "target": "", "status": ""}
    if settings.raw_backup_mount:
        try:
            target = resolve_target(settings.raw_backup_mount, view["subdir"])
        except RawBackupError as exc:
            view["status"] = str(exc)
        else:
            view["target"] = str(target.base)
            view["source"] = target.mount.source
            view["fstype"] = target.mount.fstype
    return view


def _form_storage(ctx: RequestContext) -> tuple[str, str]:
    """Mount and subfolder from the form, validated against what is offered."""
    mount = (ctx.form.get("raw_backup_mount") or "").strip()
    if mount and mount not in {m.name for m in network_mounts()}:
        raise FormError("Dieser Netzwerkspeicher ist nicht (mehr) vorhanden. Bitte neu auswählen.")
    try:
        subdir = validate_subdir(ctx.form.get("raw_backup_subdir") or DEFAULT_SUBDIR)
    except RawBackupError as exc:
        raise FormError(str(exc))
    return mount, subdir


def _run_storage_check(ctx: RequestContext, mount: str, subdir: str) -> StorageCheck:
    """Test the storage, keep the result for the page, log it."""
    try:
        check = check_storage(resolve_target(mount, subdir))
    except RawBackupError as exc:
        check = StorageCheck((CheckStep("Netzwerkspeicher eingebunden", False, str(exc)),), "", "")
    get_state().update(raw_backup_check={
        "ok": check.ok, "mount": mount, "subdir": subdir, "checked_at": check.checked_at,
        "test_file": check.test_file, "steps": [{"label": s.label, "ok": s.ok, "detail": s.detail} for s in check.steps],
    })
    failed = next((s for s in check.steps if not s.ok), None)
    log_event(ctx.db_path, "raw_backup", "storage_test", "passed" if check.ok else "failed",
              f"Speicher {mount or '–'}, Unterordner {subdir}"
              + (f"; Testdatei {check.test_file}" if check.test_file else "")
              + (f"; Fehler bei «{failed.label}»: {failed.detail}" if failed else ""))
    return check


def _test_raw_backup(ctx: RequestContext) -> None:
    mount, subdir = _form_storage(ctx)
    if not mount:
        raise FormError("Bitte zuerst einen Netzwerkspeicher wählen.")
    check = _run_storage_check(ctx, mount, subdir)
    if check.ok:
        get_state().set_flash(f"Speichertest bestanden. Die Testdatei bleibt liegen: {check.test_file}", ok=True)
    else:
        failed = next(s for s in check.steps if not s.ok)
        get_state().set_flash(f"Speichertest fehlgeschlagen bei «{failed.label}»: {failed.detail}", ok=False)


def _save_raw_backup(ctx: RequestContext) -> None:
    current = get_operation_settings(ctx.db_path)
    mode = (ctx.form.get("raw_backup_mode") or "").strip()
    if mode not in (RAW_BACKUP_MODE_OFF, RAW_BACKUP_MODE_REQUIRED):
        raise FormError("Ungültige Auswahl bei 'Rohdatensicherung'.")
    mount, subdir = _form_storage(ctx)
    if mode == RAW_BACKUP_MODE_REQUIRED:
        # Switching on is only possible when the storage passes the test right now.
        try:
            target = resolve_target(mount, subdir)
        except RawBackupError as exc:
            raise FormError(str(exc))
        check = _run_storage_check(ctx, mount, subdir)
        if not check.ok:
            failed = next(s for s in check.steps if not s.ok)
            raise FormError(f"Nicht umgeschaltet: Speichertest fehlgeschlagen bei «{failed.label}»: {failed.detail}")
    updated = replace(current, raw_backup_mode=mode, raw_backup_mount=mount, raw_backup_subdir=subdir)
    save_operation_settings(ctx.db_path, updated)
    detail = f"Modus {mode}, Speicher {mount or '–'}, Unterordner {subdir}"
    log_event(ctx.db_path, "raw_backup", "settings", "changed",
              f"vorher: Modus {current.raw_backup_mode}, Speicher {current.raw_backup_mount or '–'}, "
              f"Unterordner {current.raw_backup_subdir}; neu: {detail}")
    if mode == RAW_BACKUP_MODE_REQUIRED:
        get_state().set_flash(f"Speichertest bestanden. Rohdatensicherung ist Pflicht: Ziel {target.base}.", ok=True)
    else:
        get_state().set_flash("Rohdatensicherung ausgeschaltet: Swisseldex-Originale liegen nur im lokalen Archiv.",
                              ok=False)


def _documents_dir(ctx: RequestContext):
    from shareomat.web.pages.contract import documents_dir
    return documents_dir(ctx.db_path)


def handle_get(ctx: RequestContext) -> str:
    """Render the raw backup and backup cards and a read-only technical-config summary.

    The processing settings card (meter data source, unknown meters, archiving)
    went with the CSV path in 0.6.34 (ADR-0012).
    """
    from shareomat.security.context import current_principal
    from shareomat.web.login import recent_auth_required

    settings = get_operation_settings(ctx.db_path)
    folder = backup_dir(ctx.runtime) if ctx.runtime else None
    return render_page(
        "settings/edit.html", ctx, "settings",
        settings=settings, runtime=ctx.runtime,
        backup_folder=folder, backups=list_backups(folder) if folder else [],
        backup_password_needed=recent_auth_required(current_principal()),
        raw_backup=_raw_backup_view(settings), raw_backup_modes=RAW_BACKUP_MODES,
        raw_backup_check=get_state().get().get("raw_backup_check"),
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
    """Save the raw backup settings, test them, or create a backup / confirm the password for downloads."""
    if ctx.segments == ["backup"]:
        _create_backup(ctx)
        return None
    if ctx.segments == ["raw_backup"]:
        _save_raw_backup(ctx)
        return None
    if ctx.segments == ["raw_backup", "test"]:
        _test_raw_backup(ctx)
        return None
    if ctx.segments == ["backup", "confirm"]:
        # The password itself is checked by the web server before this runs.
        get_state().set_flash("Passwort bestätigt. Sicherungen können jetzt 10 Minuten lang heruntergeladen werden.", ok=True)
        return None
    raise FormError("Unbekannte Aktion.")
