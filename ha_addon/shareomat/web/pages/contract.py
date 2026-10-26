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
        GET  /                             overview + versions list + create/edit form + Teilnehmer
        GET  /?edit=<id>                   form bound to an existing draft (in-place update)
        GET  /?copy_from=<id>              form prefilled from any version, for a NEW draft
        GET  /participants/<id>/declaration  one participant's Beitrittserklärung
        POST /settings                      save ContractSettings prefill defaults
        POST /draft/create                  insert a new draft
        POST /draft/update                   update an existing draft in place (field: version_id)
        POST /draft/delete                   delete a draft (field: version_id)
        POST /publish                        publish a draft (field: version_id)
        POST /withdraw                       withdraw a not-yet-effective published version (field: version_id)
        POST /end                            end an active contract (fields: version_id, end_date, confirm_name)
        POST /participants/add                assign a participant to the current contract version
        POST /participants/<id>/confirm       confirm a new participant's Beitrittserklärung
        POST /participants/<id>/notify        record that an existing participant was informed of this version
        POST /participants/<id>/depart        record a participant's departure

    Rendering the legal text reuses the same Jinja2 environment as every
    other page (shareomat.web.rendering._env) rather than building a
    second templating setup — contract/legal_text.html is rendered
    standalone (not extending base.html) to a plain string, which is what
    gets stored as contract_versions.contract_text_snapshot. The same
    approach renders contract/beitrittserklaerung.html for one
    participant's join declaration.
"""

from __future__ import annotations

import calendar
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

from shareomat.core.participant_roles import ParticipantRoles, participant_roles, roles_by_participant
from shareomat.database.community import get_community
from shareomat.database.contract_settings import get_contract_settings, save_contract_settings
from shareomat.database.contract_versions import (
    ContractWorkflowError,
    delete_draft,
    end_contract,
    get_contract_version,
    get_contract_version_for_date,
    get_latest_relevant_version,
    list_contract_versions,
    publish_version,
    save_draft_version,
    update_draft_version,
    withdraw_version,
)
from shareomat.database.participant_contract import (
    ParticipantContractAssignment,
    ParticipantContractError,
    assign_contract,
    get_assignment,
    has_any_assignment,
    list_assignments_for_version,
    mark_accepted,
    mark_notified,
    record_departure,
)
from shareomat.database.meters import list_meters
from shareomat.database.participants import get_participant, list_participants
from shareomat.database.supplier_tariffs import get_supplier_tariff, list_supplier_tariffs
from shareomat.leg_const import CONTRACT_STATUS_DRAFT
from shareomat.models.contract import ContractSettings, ContractVersion
from shareomat.models.participant import Participant
from shareomat.models.external_data import SupplierTariff
from shareomat.models.tariff import RATE_MODE_FLAT
from shareomat.web.pages.meters import METER_ROLE_STORAGE_HINT
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


_RAPPEN_PER_CHF = Decimal("100")


def _form_rappen(ctx: RequestContext, field_name: str, *, label: str) -> Decimal:
    """Parse a required price field entered in Rappen and convert it to CHF/kWh.

    The admin form takes prices in Rp./kWh (e.g. "11.50") since that is
    the unit LEG price sheets are usually communicated in — everywhere
    else in the app (ContractVersion, tariffs, billing) stores CHF/kWh, so
    the conversion happens once, right here, at the form boundary. Decimal
    division by 100 is exact (no float rounding), so no precision is lost.
    """
    return form_decimal(ctx, field_name, label=label) / _RAPPEN_PER_CHF


def _require_month_start(value: date, *, label: str) -> None:
    """Raise FormError unless `value` is the first day of its month (VSE: Eintritte auf Monatsersten)."""
    if value.day != 1:
        raise FormError(f"'{label}' muss auf einen Monatsersten fallen (gewählt: {value.isoformat()}).")


def _require_month_end(value: date, *, label: str) -> None:
    """Raise FormError unless `value` is the last day of its month (VSE: Austritte auf Monatsletzten)."""
    last_day = calendar.monthrange(value.year, value.month)[1]
    if value.day != last_day:
        raise FormError(f"'{label}' muss auf einen Monatsletzten fallen (gewählt: {value.isoformat()}).")


def _vnb_notice_warning(db_path, valid_from: date) -> str | None:
    """A non-blocking flash warning if `valid_from` leaves less than the usual grid-operator notice period.

    1 month for an ordinary new participant, 3 months if this is the very
    first participant ever assigned to any contract version (LEG founding
    — a smart meter typically still needs to be installed), per VSE BD
    LEG-CH 2025. This is a reminder only, never a hard block — an admin
    must still be able to record a late-notified join.
    """
    required_months = 3 if not has_any_assignment(db_path) else 1
    earliest_comfortable = date.today() + timedelta(days=30 * required_months)
    if valid_from >= earliest_comfortable:
        return None
    return (
        f"Meldefrist an den Netzbetreiber knapp — üblicherweise mindestens {required_months} "
        f"Monat(e) im Voraus (gemäss VSE BD LEG-CH 2025)."
    )


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


def _compute_rates(*, local_rate: Decimal, admin_fee: Decimal) -> Decimal:
    """Derive the Produzentenvergütung from the LEG-Bezugspreis and the Verwaltungsgebühr.

    The LEG sets its own price (LEG-Mustervertrag §3; LEG_ONE_Mehrparteien
    §12 Variante 3): consumers pay local_rate per LEG-kWh, the administration
    keeps admin_fee of it, producers receive the rest. The grid operator's
    reference price plays no part here. A fee larger than the price would
    mean a negative producer rate — rejected rather than silently clamped.
    """
    if local_rate < 0 or admin_fee < 0:
        raise FormError("LEG-Bezugspreis und Verwaltungsgebühr dürfen nicht negativ sein.")
    feed_in_rate = local_rate - admin_fee
    if feed_in_rate < 0:
        raise FormError(
            f"Die Verwaltungsgebühr ({admin_fee * _RAPPEN_PER_CHF} Rp./kWh) übersteigt den "
            f"LEG-Bezugspreis ({local_rate * _RAPPEN_PER_CHF} Rp./kWh)."
        )
    return feed_in_rate


def _reference_label(tariff: SupplierTariff) -> str:
    return f"{tariff.source} – {tariff.product}" if tariff.product else tariff.source


def _reference_from_form(ctx: RequestContext) -> tuple[str, Decimal, Decimal | None]:
    """Resolve the picked grid-operator product to an informational snapshot (label, HT/ET, NT).

    "current" keeps the snapshot the draft already had (its product may since
    have been deleted from the Tarife tab); "" means no reference.
    """
    choice = (ctx.form.get("reference_tariff_id") or "").strip()
    if not choice:
        return "", Decimal("0"), None
    if choice == "current":
        nt_raw = ctx.form.get("current_reference_price_nt") or ""
        return (
            (ctx.form.get("current_reference_product") or "").strip(),
            Decimal(ctx.form.get("current_reference_price") or "0"),
            Decimal(nt_raw) if nt_raw else None,
        )
    try:
        tariff = get_supplier_tariff(ctx.db_path, int(choice))
    except ValueError:
        tariff = None
    if tariff is None:
        raise FormError("Das gewählte Referenzprodukt wurde nicht gefunden.")
    return _reference_label(tariff), tariff.energy_rate_ht_chf_kwh or Decimal("0"), tariff.energy_rate_nt_chf_kwh


def _version_from_form(ctx: RequestContext) -> ContractVersion:
    """Build a not-yet-persisted ContractVersion from the create/edit form's fields."""
    local_rate = _form_rappen(ctx, "local_rate_chf_kwh", label="LEG-Bezugspreis")
    admin_fee = _form_rappen(ctx, "admin_fee_chf_kwh", label="Verwaltungsgebühr")
    feed_in_rate = _compute_rates(local_rate=local_rate, admin_fee=admin_fee)
    reference_product, reference_price, reference_price_nt = _reference_from_form(ctx)

    return ContractVersion(
        community_id="", version=0, status=CONTRACT_STATUS_DRAFT, contract_text_snapshot="",
        vnb_reference_price_chf_kwh=reference_price, vnb_reference_price_nt_chf_kwh=reference_price_nt,
        reference_product=reference_product,
        local_rate_chf_kwh=local_rate, feed_in_rate_chf_kwh=feed_in_rate,
        admin_fee_chf_kwh=admin_fee, rate_mode=RATE_MODE_FLAT,
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
    """A not-yet-persisted draft prefilled from the latest contract version (if any) and the settings defaults."""
    latest = get_latest_relevant_version(db_path)
    return ContractVersion(
        community_id="", version=0, status=CONTRACT_STATUS_DRAFT, contract_text_snapshot="",
        vnb_reference_price_chf_kwh=latest.vnb_reference_price_chf_kwh if latest else Decimal("0"),
        vnb_reference_price_nt_chf_kwh=latest.vnb_reference_price_nt_chf_kwh if latest else None,
        reference_product=latest.reference_product if latest else "",
        local_rate_chf_kwh=latest.local_rate_chf_kwh if latest else Decimal("0"),
        feed_in_rate_chf_kwh=latest.feed_in_rate_chf_kwh if latest else Decimal("0"),
        admin_fee_chf_kwh=latest.admin_fee_chf_kwh if latest else Decimal("0"),
        rate_mode=RATE_MODE_FLAT,
        representative_name=settings.representative_name,
        representative_address_line=settings.representative_address_line,
        representative_postal_code=settings.representative_postal_code,
        representative_city=settings.representative_city,
        representative_email=settings.representative_email,
        grid_operator=settings.grid_operator,
        distribution_method=settings.distribution_method,
    )


def _reference_choices(db_path, form_version: ContractVersion, settings: ContractSettings) -> dict:
    """Template data for picking the grid operator and its reference product (both from the Tarife tab)."""
    products = list_supplier_tariffs(db_path, include_inactive=False)
    operators = sorted(
        {t.source for t in products}
        | {o for o in (form_version.grid_operator, settings.grid_operator) if o}
    )
    selected = next(
        (t.id for t in products
         if form_version.reference_product and _reference_label(t) == form_version.reference_product),
        None,
    )
    return {
        "grid_operators": operators,
        "reference_products": [
            {
                "id": t.id, "operator": t.source, "label": _reference_label(t),
                "valid_from": t.valid_from, "valid_until": t.valid_until,
                "ht": t.energy_rate_ht_chf_kwh, "nt": t.energy_rate_nt_chf_kwh,
            }
            for t in products
        ],
        "selected_reference_id": selected,
        # The draft's reference snapshot no longer matches any offered product
        # (deleted/deactivated in the Tarife tab) -> offer to keep it as is.
        "keep_current_reference": bool(form_version.reference_product) and selected is None,
    }


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
        version=version,
    )


def render_declaration(
    assignment: ParticipantContractAssignment, participant: Participant, version: ContractVersion,
    community_name: str, roles: ParticipantRoles,
) -> str:
    """Render one participant's Beitrittserklärung for the contract version they are assigned to.

    The ticked role (beziehen/liefern/Prosumer/Speicher) and the listed
    Messpunktnummern come from the participant's active meters — a
    participant has no type of its own.
    """
    template = jinja_env.get_template("contract/beitrittserklaerung.html")
    return template.render(
        community_name=community_name, version=version, participant=participant, assignment=assignment,
        roles=roles, storage_hint=METER_ROLE_STORAGE_HINT,
    )


def handle_get(ctx: RequestContext) -> str:
    """Render the overview/Teilnehmer/create-edit-form, or one participant's declaration."""
    if len(ctx.segments) == 3 and ctx.segments[0] == "participants" and ctx.segments[2] == "declaration":
        try:
            assignment_id = int(ctx.segments[1])
        except ValueError:
            assignment_id = -1
        assignment = get_assignment(ctx.db_path, assignment_id)
        version = get_contract_version(ctx.db_path, assignment.contract_version_id) if assignment else None
        participant = get_participant(ctx.db_path, assignment.participant_id) if assignment else None
        if assignment is None or version is None or participant is None:
            return render_page("placeholders/coming_soon.html", ctx, "contract",
                                title="Beitrittserklärung nicht gefunden", description="")
        community = get_community(ctx.db_path)
        roles = participant_roles(m for m in list_meters(ctx.db_path) if m.participant_id == participant.participant_id)
        declaration_text = render_declaration(
            assignment, participant, version, community.name if community else "", roles,
        )
        return render_page(
            "contract/declaration.html", ctx, "contract",
            declaration_text=declaration_text, participant=participant, version=version,
        )

    settings = get_contract_settings(ctx.db_path)
    community = get_community(ctx.db_path)
    current_version = get_contract_version_for_date(ctx.db_path)
    versions = list_contract_versions(ctx.db_path)

    all_participants = list_participants(ctx.db_path)
    participant_lookup = {p.participant_id: p for p in all_participants}
    assignments: list[ParticipantContractAssignment] = []
    unassigned_participants = all_participants
    if current_version is not None:
        assignments = list_assignments_for_version(ctx.db_path, current_version.id)
        assigned_ids = {a.participant_id for a in assignments}
        unassigned_participants = [p for p in all_participants if p.participant_id not in assigned_ids]

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
        **_reference_choices(ctx.db_path, form_version, settings),
        assignments=assignments, participant_lookup=participant_lookup,
        unassigned_participants=unassigned_participants,
        participant_roles=roles_by_participant(list_meters(ctx.db_path)),
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
        # The version number is only assigned on insert, but the closing
        # identification block in the rendered text needs it — save first,
        # then render with the now-known number and patch the text in.
        saved = save_draft_version(ctx.db_path, version)
        saved.contract_text_snapshot = render_contract_text(saved, community.name)
        saved = update_draft_version(ctx.db_path, saved.id, saved)
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
        existing = get_contract_version(ctx.db_path, version_id)
        if existing is None:
            raise FormError(f"Vertragsversion {version_id} wurde nicht gefunden.")
        version = _version_from_form(ctx)
        version.version = existing.version
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

    if ctx.segments == ["participants", "add"]:
        current_version = get_contract_version_for_date(ctx.db_path)
        if current_version is None:
            raise FormError("Kein aktuell aktiver Vertrag — zuerst eine Vertragsversion veröffentlichen.")
        participant_id = form_required(ctx, "participant_id", label="Teilnehmer")
        valid_from = form_date(ctx, "valid_from")
        if valid_from is None:
            raise FormError("'Beitritt per' ist erforderlich.")
        _require_month_start(valid_from, label="Beitritt per")

        participant = get_participant(ctx.db_path, participant_id)
        if participant is None:
            raise FormError(f"Teilnehmer '{participant_id}' wurde nicht gefunden.")

        warning = _vnb_notice_warning(ctx.db_path, valid_from)
        assign_contract(
            ctx.db_path, participant_id, current_version.id, current_version.tariff_id,
            joined_at=valid_from, accepted_at=None,
        )
        message = f"Teilnehmer '{participant.label}' zum Vertrag hinzugefügt — Beitrittserklärung noch zu bestätigen."
        if warning:
            message = f"{message} {warning}"
        get_state().set_flash(message, ok=True)
        return None

    if len(ctx.segments) == 3 and ctx.segments[0] == "participants":
        try:
            assignment_id = int(ctx.segments[1])
        except ValueError:
            raise FormError("Ungültige Zuordnung.")
        action = ctx.segments[2]

        if action == "confirm":
            accepted_at = form_date(ctx, "accepted_at") or date.today()
            try:
                mark_accepted(ctx.db_path, assignment_id, accepted_at)
            except ParticipantContractError as exc:
                raise FormError(str(exc))
            get_state().set_flash("Beitritt bestätigt.", ok=True)
            return None

        if action == "notify":
            notified_at = form_date(ctx, "notified_at") or date.today()
            try:
                mark_notified(ctx.db_path, assignment_id, notified_at)
            except ParticipantContractError as exc:
                raise FormError(str(exc))
            get_state().set_flash("Mitteilung über die aktuelle Vertragsversion erfasst.", ok=True)
            return None

        if action == "depart":
            end_date = form_date(ctx, "end_date")
            if end_date is None:
                raise FormError("Austrittsdatum ist erforderlich.")
            _require_month_end(end_date, label="Austritt per")
            assignment = get_assignment(ctx.db_path, assignment_id)
            if assignment is None:
                raise FormError(f"Zuordnung {assignment_id} wurde nicht gefunden.")
            record_departure(ctx.db_path, assignment_id, end_date)
            get_state().set_flash(
                "Austritt erfasst. Meldung an den Netzbetreiber: üblicherweise mindestens 1 Monat im "
                "Voraus, gemäss VSE BD LEG-CH 2025.", ok=True,
            )
            return None

    raise FormError("Unbekannte Aktion.")
