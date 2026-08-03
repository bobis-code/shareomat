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
    every other admin page: the result is persisted, logged to
    data_imports, summarized in the flash message, then the page reloads
    showing the updated tables and import history.

    ElCom, SNB, and BFE results are all persisted now (elcom_tariffs,
    exchange_rates, reference_prices respectively) — a fetch is not just
    a log line, it produces a real, browsable table below its form, kept
    up to date via upsert on the next fetch of the same
    municipality/year or currency/period.

    Fetches run synchronously inside the POST request (SPARQL/REST calls
    typically take ~1s); the web server is threaded (ThreadingHTTPServer)
    so this does not block other visitors.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable, TypeVar

from shareomat.database.data_imports import list_recent_imports, record_import
from shareomat.database.elcom_tariffs import list_elcom_tariffs, save_elcom_tariffs
from shareomat.database.exchange_rates import list_exchange_rates, save_exchange_rates
from shareomat.database.external_settings import get_external_data_settings, save_external_data_settings
from shareomat.database.reference_prices import list_reference_prices, save_reference_price
from shareomat.external_data.bfe import BfeFetchError, download_reference_prices
from shareomat.external_data.elcom import ElcomFetchError, download_elcom_tariffs
from shareomat.external_data.market_forecast import MarketForecastError, download_exchange_rates
from shareomat.models.external_data import DataImportRecord, ExternalDataSettings, ReferencePrice
from shareomat.web.rendering import FormError, RequestContext, render_page
from shareomat.web.state import get_state

_T = TypeVar("_T")
_SUMMARY_ITEM_LIMIT = 12


def _summarize(items: list[_T], formatter: Callable[[_T], str], *, limit: int = _SUMMARY_ITEM_LIMIT) -> str:
    """Render up to `limit` items as a compact 'a=1, b=2, …' string for a data_imports detail field."""
    shown = ", ".join(formatter(item) for item in items[:limit])
    if len(items) > limit:
        shown += f", … ({len(items) - limit} weitere)"
    return shown


def handle_get(ctx: RequestContext) -> str:
    """Render settings, fetched-data tables, and the recent-imports log."""
    settings = get_external_data_settings(ctx.db_path)
    imports = list_recent_imports(ctx.db_path)
    pv_reference_prices = list_reference_prices(ctx.db_path, technology="pv")
    exchange_rates = list_exchange_rates(ctx.db_path, currency="EUR")
    elcom_tariffs = list_elcom_tariffs(
        ctx.db_path,
        municipality_bfs_number=settings.municipality_bfs_number or None,
    )
    return render_page(
        "external_data/edit.html", ctx, "external_data",
        settings=settings, imports=imports, now_year=date.today().year,
        pv_reference_prices=pv_reference_prices,
        exchange_rates=exchange_rates,
        elcom_tariffs=elcom_tariffs,
    )


def handle_post(ctx: RequestContext) -> str | None:
    """Save settings or run the requested on-demand external-data fetch."""
    action = ctx.segments[0] if ctx.segments else ""

    if action == "settings":
        save_external_data_settings(ctx.db_path, ExternalDataSettings(
            entsoe_api_token=(ctx.form.get("entsoe_api_token") or "").strip(),
            municipality_bfs_number=(ctx.form.get("municipality_bfs_number") or "").strip(),
        ))
        get_state().set_flash("Grundeinstellungen gespeichert.", ok=True)
        return None

    if action == "elcom":
        try:
            municipality = int(ctx.form.get("municipality_bfs_number", ""))
            year = int(ctx.form.get("year", ""))
        except ValueError:
            raise FormError("Gemeindenummer und Jahr müssen Zahlen sein.")

        # Using this municipality for a lookup also makes it the new remembered default —
        # that's the "feste Ortschaft hinterlegen" behaviour: use once, stays pre-filled next time.
        current_settings = get_external_data_settings(ctx.db_path)
        if current_settings.municipality_bfs_number != str(municipality):
            save_external_data_settings(ctx.db_path, ExternalDataSettings(
                entsoe_api_token=current_settings.entsoe_api_token,
                municipality_bfs_number=str(municipality),
            ))

        try:
            tariffs = download_elcom_tariffs(municipality, year)
        except ElcomFetchError as exc:
            record_import(ctx.db_path, DataImportRecord(
                source="ElCom", data_type="tariffs", status="error",
                fetched_at=datetime.now(timezone.utc), detail=str(exc),
            ))
            get_state().set_flash(f"ElCom-Abruf fehlgeschlagen: {exc}", ok=False)
            return None

        save_elcom_tariffs(ctx.db_path, str(municipality), year, tariffs)
        values = _summarize(tariffs, lambda t: f"{t.category}={t.energy_chf_kwh} CHF/kWh")
        record_import(ctx.db_path, DataImportRecord(
            source="ElCom", data_type="tariffs", status="ok",
            fetched_at=datetime.now(timezone.utc),
            detail=f"Gemeinde {municipality}/{year} gespeichert: {values}" if values
            else f"Gemeinde {municipality}/{year}: keine Kategorien gefunden",
        ))
        get_state().set_flash(
            f"ElCom-Tarife für Gemeinde {municipality} ({year}) abgerufen und gespeichert: "
            f"{len(tariffs)} Kategorie(n) — siehe Tabelle unten.",
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

        save_exchange_rates(ctx.db_path, "EUR", rates)
        values = _summarize(rates, lambda r: f"{r.period}={r.rate_chf_per_eur}")
        record_import(ctx.db_path, DataImportRecord(
            source="SNB", data_type="exchange_rates", status="ok",
            fetched_at=datetime.now(timezone.utc),
            detail=f"EUR/CHF {from_period}..{to_period} gespeichert: {values}" if values
            else f"EUR/CHF {from_period}..{to_period}: keine Werte gefunden",
        ))
        get_state().set_flash(
            f"SNB-Wechselkurse abgerufen und gespeichert: {len(rates)} Monatswert(e) — siehe Tabelle unten.",
            ok=True,
        )
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
