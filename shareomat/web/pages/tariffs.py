# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/tariffs.py

Purpose:
    "Tarife" admin page. Two kinds of prices, kept strictly apart:

    1. Netzbetreiber-Tarife (grid operator energy products, e.g. EBL Grau
       2027) — entered or imported HERE, and only picked as an
       informational reference in the contract. They never set the LEG price.
    2. LEG-Preise — created by publishing a contract version (Vertrag);
       listed here read-only so prices are never entered twice.

Notes:
    Routes (relative to /tariffs):
        GET  /                         both lists
        POST /vnb/import               import a grid operator tariff JSON (field: url)
        POST /vnb/new                  add one grid operator product by hand
        POST /vnb/<id>/toggle          activate/deactivate a grid operator product
        POST /vnb/<id>/delete          delete a grid operator product
        GET/POST /new, /<id>/edit, /<id>/toggle
                                       legacy manual LEG tariff routes (setup wizard
                                       fallback without a contract; not linked in the UI)

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from shareomat.database.supplier_tariffs import (
    create_supplier_tariff,
    delete_supplier_tariff,
    get_supplier_tariff,
    list_supplier_tariffs,
    replace_supplier_tariffs,
    set_supplier_tariff_active,
)
from shareomat.database.tariffs import (
    create_tariff,
    get_tariff,
    get_tariff_for_date,
    list_tariffs,
    set_tariff_active,
    update_tariff,
)
from shareomat.external_data.vnb_tariff_json import (
    EBL_TARIFF_JSON_URL,
    VnbTariffFetchError,
    VnbTariffFormatError,
    fetch_vnb_tariff_json,
    parse_vnb_tariff_json,
)
from shareomat.models.external_data import SupplierTariff
from shareomat.models.tariff import Tariff
from shareomat.web.rendering import (
    FormError,
    RequestContext,
    form_bool,
    form_date,
    form_decimal,
    form_required,
    render_page,
)
from shareomat.web.state import get_state


def _tariff_from_form(ctx: RequestContext) -> Tariff:
    valid_from = form_date(ctx, "valid_from")
    if valid_from is None:
        raise FormError("'Gültig ab' ist erforderlich.")
    return Tariff(
        local_rate_chf_kwh=form_decimal(ctx, "local_rate_chf_kwh", label="Lokaltarif CHF/kWh"),
        grid_rate_chf_kwh=form_decimal(ctx, "grid_rate_chf_kwh", label="Netztarif CHF/kWh"),
        feed_in_rate_chf_kwh=form_decimal(ctx, "feed_in_rate_chf_kwh", label="Einspeisetarif CHF/kWh"),
        name=form_required(ctx, "name", label="Bezeichnung"),
        valid_from=valid_from,
        valid_until=form_date(ctx, "valid_until"),
        active=form_bool(ctx, "active"),
    )


_RAPPEN_PER_CHF = Decimal("100")


def _supplier_tariff_from_form(ctx: RequestContext) -> SupplierTariff:
    valid_from = form_date(ctx, "valid_from")
    if valid_from is None:
        raise FormError("'Gültig ab' ist erforderlich.")
    nt_raw = (ctx.form.get("energy_rate_nt") or "").strip()
    return SupplierTariff(
        source=form_required(ctx, "source", label="Netzbetreiber"),
        product=form_required(ctx, "product", label="Produkt"),
        valid_from=valid_from,
        valid_until=form_date(ctx, "valid_until"),
        energy_rate_ht_chf_kwh=form_decimal(ctx, "energy_rate_ht", label="Energiepreis") / _RAPPEN_PER_CHF,
        energy_rate_nt_chf_kwh=(
            form_decimal(ctx, "energy_rate_nt", label="Energiepreis Niedertarif") / _RAPPEN_PER_CHF
            if nt_raw else None
        ),
    )


def _vnb_id(ctx: RequestContext) -> int:
    try:
        return int(ctx.segments[1])
    except (IndexError, ValueError):
        raise FormError("Ungültige Tarif-ID.")


def handle_get(ctx: RequestContext) -> str:
    """Route to the tariff list, new-tariff form, or edit form."""
    today = date.today()

    if not ctx.segments:
        tariffs = list_tariffs(ctx.db_path)
        current = get_tariff_for_date(ctx.db_path, today)
        return render_page(
            "tariffs/list.html", ctx, "tariffs",
            tariffs=tariffs, current=current, today=today.isoformat(),
            vnb_tariffs=list_supplier_tariffs(ctx.db_path),
            default_import_url=EBL_TARIFF_JSON_URL.format(year=today.year + 1),
        )

    if ctx.segments == ["new"]:
        return render_page("tariffs/form.html", ctx, "tariffs", tariff=None, tariff_id=None)

    if len(ctx.segments) == 2 and ctx.segments[1] == "edit":
        try:
            tariff_id = int(ctx.segments[0])
        except ValueError:
            tariff_id = -1
        tariff = get_tariff(ctx.db_path, tariff_id)
        if tariff is None:
            return render_page("placeholders/coming_soon.html", ctx, "tariffs",
                                title="Tarif nicht gefunden", description="")
        return render_page("tariffs/form.html", ctx, "tariffs", tariff=tariff, tariff_id=tariff_id)

    return render_page("placeholders/coming_soon.html", ctx, "tariffs",
                        title="Seite nicht gefunden", description="")


def handle_post(ctx: RequestContext) -> str | None:
    """Create, update, or toggle a tariff, depending on the sub-path."""
    if ctx.segments == ["vnb", "import"]:
        url = form_required(ctx, "url", label="Adresse der Tarifdatei")
        try:
            operator, products = parse_vnb_tariff_json(fetch_vnb_tariff_json(url))
        except (VnbTariffFetchError, VnbTariffFormatError) as exc:
            raise FormError(str(exc))
        stored = 0
        for valid_from in sorted({p.valid_from for p in products}):
            stored += replace_supplier_tariffs(
                ctx.db_path, operator, valid_from, [p for p in products if p.valid_from == valid_from],
            )
        get_state().set_flash(f"{stored} Energieprodukt(e) von {operator} importiert.", ok=True)
        return None

    if ctx.segments == ["vnb", "new"]:
        tariff = create_supplier_tariff(ctx.db_path, _supplier_tariff_from_form(ctx))
        get_state().set_flash(f"Tarif '{tariff.product}' von {tariff.source} angelegt.", ok=True)
        return None

    if len(ctx.segments) == 3 and ctx.segments[0] == "vnb" and ctx.segments[2] in ("toggle", "delete"):
        tariff = get_supplier_tariff(ctx.db_path, _vnb_id(ctx))
        if tariff is None:
            raise FormError("Tarif wurde nicht gefunden.")
        if ctx.segments[2] == "delete":
            delete_supplier_tariff(ctx.db_path, tariff.id)
            get_state().set_flash(f"Tarif '{tariff.product}' gelöscht.", ok=True)
        else:
            set_supplier_tariff_active(ctx.db_path, tariff.id, not tariff.active)
            state = "aktiviert" if not tariff.active else "deaktiviert"
            get_state().set_flash(f"Tarif '{tariff.product}' {state}.", ok=True)
        return None

    if ctx.segments == ["new"]:
        create_tariff(ctx.db_path, _tariff_from_form(ctx))
        get_state().set_flash("Tarif angelegt.", ok=True)
        return None

    if len(ctx.segments) == 2 and ctx.segments[1] == "edit":
        try:
            tariff_id = int(ctx.segments[0])
        except ValueError:
            raise FormError("Ungültige Tarif-ID.")
        try:
            update_tariff(ctx.db_path, tariff_id, _tariff_from_form(ctx))
        except ValueError as exc:
            raise FormError(str(exc))
        get_state().set_flash("Tarif gespeichert.", ok=True)
        return None

    if len(ctx.segments) == 2 and ctx.segments[1] == "toggle":
        try:
            tariff_id = int(ctx.segments[0])
        except ValueError:
            raise FormError("Ungültige Tarif-ID.")
        tariff = get_tariff(ctx.db_path, tariff_id)
        if tariff is None:
            raise FormError("Tarif wurde nicht gefunden.")
        set_tariff_active(ctx.db_path, tariff_id, not tariff.active)
        state = "aktiviert" if not tariff.active else "deaktiviert"
        get_state().set_flash(f"Tarif {state}.", ok=True)
        return None

    raise FormError("Unbekannte Aktion.")
