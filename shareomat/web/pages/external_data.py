# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/external_data.py

Purpose:
    "Externe Daten" admin page: ENTSO-E API token, on-demand ElCom tariff
    lookup, on-demand SNB exchange rate lookup, on-demand BFE official
    reference-price fetch, and a log of recent external data fetches.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Every fetch action follows the same redirect-after-POST pattern as
    every other admin page: the result is logged to data_imports and
    summarized in the flash message, then the page reloads showing the
    updated import history — no special-cased inline rendering.

    Fetches run synchronously inside the POST request (SPARQL/REST calls
    typically take ~1s); the web server is threaded (ThreadingHTTPServer)
    so this does not block other visitors.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from shareomat.database.data_imports import list_recent_imports, record_import
from shareomat.database.external_settings import get_external_data_settings, save_external_data_settings
from shareomat.database.reference_prices import list_reference_prices, save_reference_price
from shareomat.external_data.bfe import BfeFetchError, download_reference_prices
from shareomat.external_data.elcom import ElcomFetchError, download_elcom_tariffs
from shareomat.external_data.market_forecast import MarketForecastError, download_exchange_rates
from shareomat.models.external_data import DataImportRecord, ExternalDataSettings, ReferencePrice
from shareomat.web.rendering import FormError, RequestContext, render_page
from shareomat.web.state import get_state


def handle_get(ctx: RequestContext) -> str:
    settings = get_external_data_settings(ctx.db_path)
    imports = list_recent_imports(ctx.db_path)
    pv_reference_prices = list_reference_prices(ctx.db_path, technology="pv")[:8]
    return render_page(
        "external_data/edit.html", ctx, "external_data",
        settings=settings, imports=imports, now_year=date.today().year,
        pv_reference_prices=pv_reference_prices,
    )


def handle_post(ctx: RequestContext) -> str | None:
    action = ctx.segments[0] if ctx.segments else ""

    if action == "token":
        save_external_data_settings(ctx.db_path, ExternalDataSettings(
            entsoe_api_token=(ctx.form.get("entsoe_api_token") or "").strip(),
        ))
        get_state().set_flash("ENTSO-E-Token gespeichert.", ok=True)
        return None

    if action == "elcom":
        try:
            municipality = int(ctx.form.get("municipality_bfs_number", ""))
            year = int(ctx.form.get("year", ""))
        except ValueError:
            raise FormError("Gemeindenummer und Jahr müssen Zahlen sein.")

        try:
            tariffs = download_elcom_tariffs(municipality, year)
        except ElcomFetchError as exc:
            record_import(ctx.db_path, DataImportRecord(
                source="ElCom", data_type="tariffs", status="error",
                fetched_at=datetime.now(timezone.utc), detail=str(exc),
            ))
            get_state().set_flash(f"ElCom-Abruf fehlgeschlagen: {exc}", ok=False)
            return None

        record_import(ctx.db_path, DataImportRecord(
            source="ElCom", data_type="tariffs", status="ok",
            fetched_at=datetime.now(timezone.utc),
            detail=f"{len(tariffs)} Kategorie(n) für Gemeinde {municipality}/{year}",
        ))
        get_state().set_flash(
            f"ElCom-Tarife für Gemeinde {municipality} ({year}) abgerufen: {len(tariffs)} Kategorie(n).",
            ok=True,
        )
        return None

    if action == "snb":
        from_period = (ctx.form.get("from_period") or "").strip()
        to_period = (ctx.form.get("to_period") or "").strip()

        try:
            rates = download_exchange_rates(from_period, to_period)
        except MarketForecastError as exc:
            record_import(ctx.db_path, DataImportRecord(
                source="SNB", data_type="exchange_rates", status="error",
                fetched_at=datetime.now(timezone.utc), detail=str(exc),
            ))
            get_state().set_flash(f"SNB-Abruf fehlgeschlagen: {exc}", ok=False)
            return None

        record_import(ctx.db_path, DataImportRecord(
            source="SNB", data_type="exchange_rates", status="ok",
            fetched_at=datetime.now(timezone.utc),
            detail=f"{len(rates)} Monatswert(e) für {from_period}..{to_period}",
        ))
        get_state().set_flash(f"SNB-Wechselkurse abgerufen: {len(rates)} Monatswert(e).", ok=True)
        return None

    if action == "bfe":
        period_type = ctx.form.get("period_type", "quarterly")
        if period_type not in ("monthly", "quarterly"):
            raise FormError("Ungültiger Zeitraum-Typ.")

        try:
            rows = download_reference_prices(period_type=period_type)
        except BfeFetchError as exc:
            # Deliberately does NOT touch shareomat.database.reference_prices — a failed
            # fetch must never overwrite the last successfully stored (and displayed) values.
            record_import(ctx.db_path, DataImportRecord(
                source="BFE", data_type=f"reference_prices_{period_type}", status="error",
                fetched_at=datetime.now(timezone.utc), detail=str(exc),
            ))
            get_state().set_flash(f"BFE-Abruf fehlgeschlagen (letzter bekannter Stand bleibt gültig): {exc}", ok=False)
            return None

        for row in rows:
            if row.technology != "pv":
                continue  # only PV reference prices are stored today; other technologies are fetched but not persisted yet
            save_reference_price(ctx.db_path, ReferencePrice(
                technology=row.technology, period_start=row.period_start, period_end=row.period_end,
                price_chf_kwh=row.price_chf_kwh, source="BFE", is_official=True,
                published_at=date.today(),
            ))
        record_import(ctx.db_path, DataImportRecord(
            source="BFE", data_type=f"reference_prices_{period_type}", status="ok",
            fetched_at=datetime.now(timezone.utc),
            detail=f"{len(rows)} Zeile(n) ({period_type}), PV-Werte gespeichert",
        ))
        get_state().set_flash(f"BFE-Referenzpreise abgerufen: {len(rows)} Zeile(n).", ok=True)
        return None

    raise FormError("Unbekannte Aktion.")
