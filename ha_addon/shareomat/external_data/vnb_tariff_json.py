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
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal

from shareomat.models.external_data import SupplierTariff

_TIMEOUT_SECONDS = 20
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
    return Decimal(str(value))


def parse_vnb_tariff_json(data: dict) -> tuple[str, list[SupplierTariff]]:
    """Return (operator short name, energy products) from a parsed tariff JSON document."""
    if not isinstance(data, dict) or "tariffs" not in data or "dsoName" not in data:
        raise VnbTariffFormatError("Unbekanntes Tarifformat: 'dsoName' oder 'tariffs' fehlt.")
    operator = operator_short_name(str(data["dsoName"]))

    products: list[SupplierTariff] = []
    for entry in data["tariffs"]:
        if entry.get("tariffType") != "electricity":
            continue
        try:
            name = str(entry["tariffName"]).strip()
            valid_from = date.fromisoformat(entry["startDate"])
            valid_until = date.fromisoformat(entry["endDate"]) if entry.get("endDate") else None
            prices = entry["prices"]
            energy = [e for e in prices.get("energy", []) if e.get("priceUnit") == "CHF/kWh"]
        except (KeyError, TypeError, ValueError) as exc:
            raise VnbTariffFormatError(f"Tarif '{entry.get('tariffName', '?')}' ist unvollständig: {exc}") from exc
        if not energy:
            raise VnbTariffFormatError(f"Tarif '{name}' hat keinen Energiepreis in CHF/kWh.")

        window_prices = [_decimal(e["price"]) for e in energy]
        high, low = max(window_prices), min(window_prices)
        base = prices.get("base") or {}
        base_year = _decimal(base["price"]) * 12 if base.get("priceUnit") == "CHF/M" and base.get("price") else None

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
    """Download and decode a tariff JSON file."""
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise VnbTariffFetchError(f"Tarifdatei konnte nicht geladen werden: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise VnbTariffFormatError(f"Tarifdatei ist kein gültiges JSON: {exc}") from exc
