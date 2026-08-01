# -*- coding: utf-8 -*-
"""
File: shareomat/external_data/elcom.py

Purpose:
    Query the official ElCom (Swiss Federal Electricity Commission)
    electricity tariff registry via its public LINDAS SPARQL endpoint —
    grid operator lookup, tariff components per municipality/category,
    and comparison against a stored supplier tariff.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Endpoint and RDF shape verified live on 2026-08-01 against
    https://lindas.admin.ch/query (the ElCom electricity-price cube at
    https://energy.ld.admin.ch/elcom/electricityprice). Source values are
    in Rappen/kWh (fixcosts in CHF/year); this module converts to CHF/kWh
    at the boundary so results are directly comparable to
    shareomat.models.tariff.Tariff / SupplierTariff.

    No API key required — the endpoint is public. Requests use only the
    Python standard library (urllib), matching the project's
    dependency-light approach.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal

logger = logging.getLogger(__name__)

_SPARQL_ENDPOINT = "https://lindas.admin.ch/query"
_TIMEOUT_SECONDS = 20
_USER_AGENT = "Shareomat/1 (+https://github.com/bobis-code/shareomat)"

_PREFIXES = (
    "PREFIX schema: <http://schema.org/>\n"
    "PREFIX cube: <https://cube.link/>\n"
    "PREFIX elcom: <https://energy.ld.admin.ch/elcom/electricityprice/dimension/>\n"
)

_OBSERVATION_SET = "<https://energy.ld.admin.ch/elcom/electricityprice/observation/>"

# Household reference category ElCom itself uses for its headline comparisons.
DEFAULT_CATEGORY = "H4"


class ElcomFetchError(Exception):
    """Raised when the LINDAS SPARQL endpoint cannot be reached or returns an error."""


@dataclass
class ElcomTariffComponents:
    """One ElCom tariff observation, converted to CHF/kWh (fixcosts stays CHF/year)."""

    category: str
    energy_chf_kwh: Decimal
    grid_chf_kwh: Decimal
    aidfee_chf_kwh: Decimal
    community_fees_chf_kwh: Decimal
    total_chf_kwh: Decimal
    fixcosts_chf_year: Decimal
    operator_iri: str | None
    operator_name: str | None


def _sparql_query(query: str) -> list[dict[str, str]]:
    """Run a SPARQL SELECT query, returning each result row as {var: value}."""
    data = urllib.parse.urlencode({"query": query}).encode("utf-8")
    request = urllib.request.Request(
        _SPARQL_ENDPOINT,
        data=data,
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": _USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ElcomFetchError(f"ElCom-Abfrage fehlgeschlagen: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ElcomFetchError(f"ElCom-Antwort konnte nicht gelesen werden: {exc}") from exc

    return [
        {key: binding["value"] for key, binding in row.items()}
        for row in payload.get("results", {}).get("bindings", [])
    ]


def _rappen_to_chf(value: str) -> Decimal:
    """ElCom publishes per-kWh components in Rappen; Shareomat stores CHF/kWh everywhere."""
    return Decimal(value) / 100


def download_elcom_tariffs(
    municipality_bfs_number: int, year: int, *, product: str = "standard",
) -> list[ElcomTariffComponents]:
    """Fetch every published tariff category for one municipality/year from ElCom."""
    query = _PREFIXES + f"""
SELECT ?category ?energy ?grid ?aidfee ?community_fees ?fixcosts ?variablecosts ?operator
WHERE {{
    {_OBSERVATION_SET} cube:observation ?observation.
    ?observation
      elcom:category/schema:name ?category;
      elcom:municipality <https://ld.admin.ch/municipality/{int(municipality_bfs_number)}>;
      elcom:period "{int(year)}"^^<http://www.w3.org/2001/XMLSchema#gYear>;
      elcom:product <https://energy.ld.admin.ch/elcom/electricityprice/product/{product}>;
      elcom:fixcosts ?fixcosts;
      elcom:total ?variablecosts;
      elcom:gridusage ?grid;
      elcom:energy ?energy;
      elcom:charge ?community_fees;
      elcom:aidfee ?aidfee.
    OPTIONAL {{ ?observation elcom:operator ?operator. }}
}}
"""
    rows = _sparql_query(query)
    operator_names: dict[str, str | None] = {}
    results: list[ElcomTariffComponents] = []
    for row in rows:
        operator_iri = row.get("operator")
        if operator_iri and operator_iri not in operator_names:
            operator_names[operator_iri] = _operator_name(operator_iri)
        results.append(ElcomTariffComponents(
            category=row["category"],
            energy_chf_kwh=_rappen_to_chf(row["energy"]),
            grid_chf_kwh=_rappen_to_chf(row["grid"]),
            aidfee_chf_kwh=_rappen_to_chf(row["aidfee"]),
            community_fees_chf_kwh=_rappen_to_chf(row["community_fees"]),
            total_chf_kwh=_rappen_to_chf(row["variablecosts"]),
            fixcosts_chf_year=Decimal(row["fixcosts"]),
            operator_iri=operator_iri,
            operator_name=operator_names.get(operator_iri) if operator_iri else None,
        ))
    logger.info(
        "ElCom: fetched %d tariff categor%s for municipality %s / %d",
        len(results), "y" if len(results) == 1 else "ies", municipality_bfs_number, year,
    )
    return results


def _operator_name(operator_iri: str) -> str | None:
    """Resolve a grid operator IRI to its display name."""
    query = f"PREFIX schema: <http://schema.org/>\nSELECT ?name WHERE {{ <{operator_iri}> schema:name ?name. }}"
    rows = _sparql_query(query)
    return rows[0]["name"] if rows else None


def find_grid_operator(municipality_bfs_number: int, year: int) -> dict[str, str] | None:
    """Return {'operator_iri', 'operator_name'} for the grid operator serving a municipality."""
    for tariff in download_elcom_tariffs(municipality_bfs_number, year):
        if tariff.operator_iri:
            return {"operator_iri": tariff.operator_iri, "operator_name": tariff.operator_name or ""}
    return None


def get_elcom_tariff(
    municipality_bfs_number: int, year: int, category: str = DEFAULT_CATEGORY,
) -> ElcomTariffComponents | None:
    """Return the ElCom tariff for one specific category (default: H4 household reference)."""
    for tariff in download_elcom_tariffs(municipality_bfs_number, year):
        if tariff.category == category:
            return tariff
    return None


def compare_with_supplier_tariff(
    municipality_bfs_number: int, year: int, category: str, stored_energy_chf_kwh: Decimal,
) -> dict[str, object]:
    """Compare a stored supplier energy rate against the live ElCom value for the same category.

    Returns a dict with the live value, the stored value, the absolute
    difference, and whether they still match closely (within 1 Rappen/kWh) —
    a simple staleness signal for "does our stored EBL tariff still look
    right?", not an authoritative reconciliation.
    """
    live = get_elcom_tariff(municipality_bfs_number, year, category)
    if live is None:
        return {"status": "no_data", "category": category}

    difference = (live.energy_chf_kwh - stored_energy_chf_kwh).copy_abs()
    return {
        "status": "ok",
        "category": category,
        "live_energy_chf_kwh": live.energy_chf_kwh,
        "stored_energy_chf_kwh": stored_energy_chf_kwh,
        "difference_chf_kwh": difference,
        "matches": difference <= Decimal("0.01"),
        "operator_name": live.operator_name,
    }
