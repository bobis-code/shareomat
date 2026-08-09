# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/contract.py

Purpose:
    "Vertrag" admin page: the LEG contract is the source of truth for LEG
    prices. A draft is freely editable; publishing snapshots it, enforces
    the contractually required notice period, and creates the tariff it
    describes.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Routes (relative to /contract, dispatched via ctx.segments):
        GET  /                      overview + versions list + create/edit form
        GET  /?edit=<id>            form bound to an existing draft (in-place update)
        GET  /?copy_from=<id>       form prefilled from any version, for a NEW draft
        POST /settings               save ContractSettings prefill defaults
        POST /draft/create           insert a new draft
        POST /draft/update            update an existing draft in place (field: version_id)
        POST /draft/delete            delete a draft (field: version_id)
        POST /publish                 publish a draft (field: version_id)
        POST /withdraw                withdraw a not-yet-effective published version (field: version_id)
        POST /end                     end an active contract (fields: version_id, end_date, confirm_name)

    Rendering the legal text reuses the same Jinja2 environment as every
    other page (shareomat.web.rendering._env) rather than building a
    second templating setup — contract/legal_text.html is rendered
    standalone (not extending base.html) to a plain string, which is what
    gets stored as contract_versions.contract_text_snapshot.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation

from shareomat.database.community import get_community
from shareomat.database.contract_settings import get_contract_settings, save_contract_settings
from shareomat.database.contract_versions import (
    ContractWorkflowError,
    delete_draft,
    end_contract,
    get_contract_version,
    get_contract_version_for_date,
    list_contract_versions,
    publish_version,
    save_draft_version,
    update_draft_version,
    withdraw_version,
)
from shareomat.database.tariffs import get_tariff_for_date
from shareomat.leg_const import CONTRACT_STATUS_DRAFT
from shareomat.models.contract import ContractSettings, ContractVersion
from shareomat.models.tariff import RATE_MODE_FLAT
from shareomat.web.rendering import FormError, RequestContext, form_date, form_decimal, form_required, render_page
from shareomat.web.rendering import _env as jinja_env
from shareomat.web.state import get_state


def _form_int(ctx: RequestContext, field_name: str, *, default: int) -> int:
    """Parse an optional positive integer form field, falling back to `default` when blank."""
    raw = (ctx.form.get(field_name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise FormError(f"'{field_name}' muss eine ganze Zahl sein.")
    if value <= 0:
        raise FormError(f"'{field_name}' muss mindestens 1 sein.")
    return value


def _form_decimal_optional(ctx: RequestContext, field_name: str) -> Decimal | None:
    """Parse an optional decimal form field (used for the NT rates), '' -> None."""
    raw = (ctx.form.get(field_name) or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        raise FormError(f"Ungültiger Wert bei '{field_name}': '{raw}'")


def _settings_from_form(ctx: RequestContext) -> ContractSettings:
    return ContractSettings(
        representative_name=(ctx.form.get("representative_name") or "").strip(),
        representative_address_line=(ctx.form.get("representative_address_line") or "").strip(),
        representative_postal_code=(ctx.form.get("representative_postal_code") or "").strip(),
        representative_city=(ctx.form.get("representative_city") or "").strip(),
        representative_email=(ctx.form.get("representative_email") or "").strip(),
        grid_operator=form_required(ctx, "grid_operator", label="Netzbetreiber"),
        distribution_method=(ctx.form.get("distribution_method") or "proportional zum Verbrauch").strip(),
    )


def _version_from_form(ctx: RequestContext) -> ContractVersion:
    """Build a not-yet-persisted ContractVersion from the create/edit form's fields."""
    return ContractVersion(
        community_id="", version=0, status=CONTRACT_STATUS_DRAFT, contract_text_snapshot="",
        local_rate_chf_kwh=form_decimal(ctx, "local_rate_chf_kwh", label="LEG-Bezugspreis"),
        local_rate_nt_chf_kwh=_form_decimal_optional(ctx, "local_rate_nt_chf_kwh"),
        feed_in_rate_chf_kwh=form_decimal(ctx, "feed_in_rate_chf_kwh", label="Produzentenvergütung"),
        feed_in_rate_nt_chf_kwh=_form_decimal_optional(ctx, "feed_in_rate_nt_chf_kwh"),
        admin_fee_chf_kwh=form_decimal(ctx, "admin_fee_chf_kwh", label="Verwaltungsgebühr"),
        rate_mode=(ctx.form.get("rate_mode") or RATE_MODE_FLAT),
        representative_name=(ctx.form.get("representative_name") or "").strip(),
        representative_address_line=(ctx.form.get("representative_address_line") or "").strip(),
        representative_postal_code=(ctx.form.get("representative_postal_code") or "").strip(),
        representative_city=(ctx.form.get("representative_city") or "").strip(),
        representative_email=(ctx.form.get("representative_email") or "").strip(),
        grid_operator=form_required(ctx, "grid_operator", label="Netzbetreiber"),
        distribution_method=(ctx.form.get("distribution_method") or "proportional zum Verbrauch").strip(),
        price_notice_period_months=_form_int(ctx, "price_notice_period_months", default=4),
        contract_notice_period_months=_form_int(ctx, "contract_notice_period_months", default=6),
        valid_from=form_date(ctx, "valid_from"),
        valid_until=form_date(ctx, "valid_until"),
    )


def _blank_form_version(db_path, settings: ContractSettings) -> ContractVersion:
    """A not-yet-persisted draft prefilled with today's tariff (if any) and the settings defaults."""
    tariff = get_tariff_for_date(db_path, date.today())
    return ContractVersion(
        community_id="", version=0, status=CONTRACT_STATUS_DRAFT, contract_text_snapshot="",
        local_rate_chf_kwh=tariff.local_rate_chf_kwh if tariff else Decimal("0"),
        local_rate_nt_chf_kwh=tariff.local_rate_nt_chf_kwh if tariff else None,
        feed_in_rate_chf_kwh=tariff.feed_in_rate_chf_kwh if tariff else Decimal("0"),
        feed_in_rate_nt_chf_kwh=tariff.feed_in_rate_nt_chf_kwh if tariff else None,
        admin_fee_chf_kwh=tariff.admin_fee_chf_kwh if tariff else Decimal("0"),
        rate_mode=tariff.rate_mode if tariff else RATE_MODE_FLAT,
        representative_name=settings.representative_name,
        representative_address_line=settings.representative_address_line,
        representative_postal_code=settings.representative_postal_code,
        representative_city=settings.representative_city,
        representative_email=settings.representative_email,
        grid_operator=settings.grid_operator,
        distribution_method=settings.distribution_method,
    )


def render_contract_text(version: ContractVersion, community_name: str) -> str:
    """Render the full LEG contract text for one version to a plain string (no page chrome/nav)."""
    template = jinja_env.get_template("contract/legal_text.html")
    return template.render(
        community_name=community_name,
        grid_operator=version.grid_operator,
        distribution_method=version.distribution_method,
        price_notice_period_months=version.price_notice_period_months,
        contract_notice_period_months=version.contract_notice_period_months,
        representative_name=version.representative_name,
        representative_address_line=version.representative_address_line,
        representative_postal_code=version.representative_postal_code,
        representative_city=version.representative_city,
        representative_email=version.representative_email,
        tariff=version,
    )


def handle_get(ctx: RequestContext) -> str:
    """Render the overview, versions list, and the create/edit/copy-from form."""
    settings = get_contract_settings(ctx.db_path)
    community = get_community(ctx.db_path)
    current_version = get_contract_version_for_date(ctx.db_path)
    versions = list_contract_versions(ctx.db_path)

    form_version: ContractVersion | None = None
    form_mode = "create"

    edit_raw = ctx.query.get("edit")
    copy_from_raw = ctx.query.get("copy_from")
    if edit_raw:
        try:
            candidate = get_contract_version(ctx.db_path, int(edit_raw))
        except ValueError:
            candidate = None
        if candidate is not None and candidate.status == CONTRACT_STATUS_DRAFT:
            form_version = candidate
            form_mode = "update"
    elif copy_from_raw:
        try:
            source = get_contract_version(ctx.db_path, int(copy_from_raw))
        except ValueError:
            source = None
        if source is not None:
            form_version = replace(
                source, id=None, version=0, status=CONTRACT_STATUS_DRAFT,
                valid_from=None, valid_until=None, tariff_id=None, supersedes_version_id=None,
            )

    if form_version is None:
        form_version = _blank_form_version(ctx.db_path, settings)

    return render_page(
        "contract/view.html", ctx, "contract",
        settings=settings, community=community, current_version=current_version,
        versions=versions, form_version=form_version, form_mode=form_mode, today=date.today(),
    )


def handle_post(ctx: RequestContext) -> str | None:
    """Dispatch settings/draft-create/draft-update/draft-delete/publish/withdraw/end actions."""
    if ctx.segments == ["settings"]:
        save_contract_settings(ctx.db_path, _settings_from_form(ctx))
        get_state().set_flash("Vorschlagswerte gespeichert.", ok=True)
        return None

    if ctx.segments == ["draft", "create"]:
        community = get_community(ctx.db_path)
        if community is None:
            raise FormError('Keine Gemeinschaft eingerichtet — zuerst unter "Gemeinschaft" anlegen.')
        version = _version_from_form(ctx)
        version.contract_text_snapshot = render_contract_text(version, community.name)
        saved = save_draft_version(ctx.db_path, version)
        get_state().set_flash(f"Entwurf Version {saved.version} erstellt.", ok=True)
        return None

    if ctx.segments == ["draft", "update"]:
        try:
            version_id = int(ctx.form.get("version_id", ""))
        except ValueError:
            raise FormError("Ungültige Vertragsversion.")
        community = get_community(ctx.db_path)
        if community is None:
            raise FormError('Keine Gemeinschaft eingerichtet — zuerst unter "Gemeinschaft" anlegen.')
        version = _version_from_form(ctx)
        version.contract_text_snapshot = render_contract_text(version, community.name)
        try:
            update_draft_version(ctx.db_path, version_id, version)
        except ContractWorkflowError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Entwurf gespeichert.", ok=True)
        return None

    if ctx.segments == ["draft", "delete"]:
        try:
            version_id = int(ctx.form.get("version_id", ""))
        except ValueError:
            raise FormError("Ungültige Vertragsversion.")
        try:
            delete_draft(ctx.db_path, version_id)
        except ContractWorkflowError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Entwurf gelöscht.", ok=True)
        return None

    if ctx.segments == ["publish"]:
        try:
            version_id = int(ctx.form.get("version_id", ""))
        except ValueError:
            raise FormError("Ungültige Vertragsversion.")
        try:
            version = publish_version(ctx.db_path, version_id)
        except ContractWorkflowError as exc:
            raise FormError(str(exc))
        get_state().set_flash(f"Vertragsversion {version.version} veröffentlicht.", ok=True)
        return None

    if ctx.segments == ["withdraw"]:
        try:
            version_id = int(ctx.form.get("version_id", ""))
        except ValueError:
            raise FormError("Ungültige Vertragsversion.")
        try:
            withdraw_version(ctx.db_path, version_id)
        except ContractWorkflowError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Veröffentlichung zurückgezogen — wieder ein Entwurf.", ok=True)
        return None

    if ctx.segments == ["end"]:
        try:
            version_id = int(ctx.form.get("version_id", ""))
        except ValueError:
            raise FormError("Ungültige Vertragsversion.")
        end_date = form_date(ctx, "end_date")
        if end_date is None:
            raise FormError("Enddatum ist erforderlich.")
        community = get_community(ctx.db_path)
        expected_name = community.name if community else ""
        if (ctx.form.get("confirm_name") or "").strip() != expected_name:
            raise FormError("Zur Bestätigung bitte den Namen der Gemeinschaft exakt eingeben.")
        try:
            end_contract(ctx.db_path, version_id, end_date)
        except ContractWorkflowError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Vertrag beendet.", ok=True)
        return None

    raise FormError("Unbekannte Aktion.")
