# -*- coding: utf-8 -*-
"""
File: shareomat/external_data/vnb_tariff_json.py

Purpose:
    Import a grid operator's published tariff JSON (the machine-readable
    tariff file Swiss DSOs publish next to their price sheets, e.g.
    https://www.ebl.ch/hubfs/EBL%202027.json) as SupplierTariff rows — the
    reference products the "Tarife" tab offers and a contract can point to.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Format (verified against EBL 2027 on 2026-10-26): top-level `dsoName`
    and `tariffs`; each tariff has `tariffName`, `tariffType` (grid |
    electricity | metering | regional_fees), `startDate`/`endDate` and
    `prices` with `base` {price, priceUnit "CHF/M"} and `energy` — a list of
    time windows {from, to, price, priceUnit "CHF/kWh", optional months /
    weekdays}.

    Only `electricity` (= energy product, e.g. "EBL Grau") entries are
    imported: the LEG reference price is the energy component only, never
    grid usage or levies (LEG-Mustervertrag §3.1). For a multi-level product
    (Doppeltarif, Flexa) the highest window price is stored as HT and the
    lowest as NT — enough for the informational reference shown in the
    contract; the exact time windows stay in the source file.

    Raise, don't guess (ADR-0003): an unknown structure raises
    VnbTariffFormatError instead of importing partial data.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal, DecimalException

from shareomat.core.price_validation import finite_decimal, validate_period
from shareomat.external_data.safe_http import SafeFetchError, UnsafeUrlError, fetch_public_https

from shareomat.models.external_data import SupplierTariff

_TIMEOUT_SECONDS = 20
# EBL 2027 is ~0.2 MB; 5 MiB leaves ample room for larger operators.
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_USER_AGENT = "Shareomat/1 (+https://github.com/bobis-code/shareomat)"

EBL_TARIFF_JSON_URL = "https://www.ebl.ch/hubfs/EBL%20{year}.json"


class VnbTariffFormatError(ValueError):
    """Raised when a tariff file does not have the expected structure."""


class VnbTariffFetchError(Exception):
    """Raised when the tariff file cannot be downloaded."""


def operator_short_name(dso_name: str) -> str:
    """'EBL (Genossenschaft Elektra Baselland)' -> 'EBL'."""
    return dso_name.split(" (", 1)[0].strip() or dso_name.strip()


def _decimal(value) -> Decimal:
    # str() first so a JSON float like 0.098 becomes Decimal("0.098"), not its binary expansion.
    return finite_decimal(value)


def parse_vnb_tariff_json(data: dict) -> tuple[str, list[SupplierTariff]]:
    """Return (operator short name, energy products) from a parsed tariff JSON document."""
    if not isinstance(data, dict) or "tariffs" not in data or "dsoName" not in data:
        raise VnbTariffFormatError("Unbekanntes Tarifformat: 'dsoName' oder 'tariffs' fehlt.")
    if not isinstance(data["dsoName"], str) or not data["dsoName"].strip() or not isinstance(data["tariffs"], list):
        raise VnbTariffFormatError("Unbekanntes Tarifformat: Betreibername und Tarifliste erforderlich.")
    operator = operator_short_name(data["dsoName"])

    products: list[SupplierTariff] = []
    for entry in data["tariffs"]:
        if not isinstance(entry, dict):
            raise VnbTariffFormatError("Ungültiger Eintrag in der Tarifliste.")
        if entry.get("tariffType") != "electricity":
            continue
        try:
            name = str(entry["tariffName"]).strip()
            valid_from = date.fromisoformat(entry["startDate"])
            end_date = entry.get("endDate")
            valid_until = date.fromisoformat(end_date) if end_date not in (None, "") else None
            validate_period(valid_from, valid_until)
            prices = entry["prices"]
            if not isinstance(prices, dict) or not isinstance(prices.get("energy"), list):
                raise ValueError("Preisobjekt mit Energiepreisliste erforderlich")
            energy = prices["energy"]
            if any(not isinstance(e, dict) or e.get("priceUnit") != "CHF/kWh" for e in energy):
                raise ValueError("Ungültiger Energiepreis oder unbekannte Preiseinheit")
            if not energy:
                raise ValueError("Kein Energiepreis in CHF/kWh")
            window_prices = [_decimal(e["price"]) for e in energy]
            high, low = max(window_prices), min(window_prices)
            base = prices.get("base")
            base_year = None
            if base is not None:
                if not isinstance(base, dict):
                    raise ValueError("Ungültiger Grundpreis")
                if base:
                    if base.get("priceUnit") != "CHF/M":
                        raise ValueError("Unbekannte Grundpreiseinheit")
                    base_year = finite_decimal(_decimal(base["price"]) * 12)
        except (KeyError, TypeError, ValueError, DecimalException) as exc:
            raise VnbTariffFormatError(f"Tarif '{entry.get('tariffName', '?')}' ist unvollständig: {exc}") from exc

        products.append(SupplierTariff(
            source=operator,
            product=name,
            valid_from=valid_from,
            valid_until=valid_until,
            energy_rate_ht_chf_kwh=high,
            energy_rate_nt_chf_kwh=low if low != high else None,
            base_price_chf_year=base_year,
        ))

    if not products:
        raise VnbTariffFormatError("Die Datei enthält keine Energieprodukte (tariffType 'electricity').")
    return operator, products


def fetch_vnb_tariff_json(url: str) -> dict:
    """Download and decode a tariff JSON file from a public https:// address (S-05).

    The URL is typed in by an administrator, so it is treated as untrusted:
    see shareomat.external_data.safe_http for the scheme, address, redirect,
    size and time limits.
    """
    try:
        body = fetch_public_https(
            url, max_bytes=_MAX_RESPONSE_BYTES, accept="application/json",
            user_agent=_USER_AGENT, timeout=_TIMEOUT_SECONDS,
        )
    except UnsafeUrlError as exc:
        raise VnbTariffFetchError(f"Adresse nicht erlaubt: {exc}") from exc
    except SafeFetchError as exc:
        raise VnbTariffFetchError(f"Tarifdatei konnte nicht geladen werden: {exc}") from exc
    try:
        return json.loads(body.decode("utf-8-sig"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VnbTariffFormatError(f"Tarifdatei ist kein gültiges JSON: {exc}") from exc
    except RecursionError as exc:
        raise VnbTariffFormatError("Tarifdatei ist zu tief verschachtelt.") from exc
