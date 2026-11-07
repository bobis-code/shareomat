# -*- coding: utf-8 -*-
"""
File: shareomat/core/price_validation.py

Purpose:
    Validate numeric price representations without inventing sign or price caps.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TypeVar

T = TypeVar("T")


class PriceValidationError(ValueError):
    """A price or tariff period cannot be safely interpreted."""


def finite_decimal(value, *, label: str = "Preis", binary_float: bool = False) -> Decimal:
    """Accept finite decimal values; bool and containers are never numbers here.

    binary_float is only for settlement rates consumed by the existing
    float core. It is a representation limit, not an economic price cap.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PriceValidationError(f"{label}: eine endliche Dezimalzahl ist erforderlich.")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PriceValidationError(f"{label}: ungültige Dezimalzahl.") from exc
    if not result.is_finite():
        raise PriceValidationError(f"{label}: NaN und Infinity sind nicht zulässig.")
    if binary_float:
        converted = float(result)
        if not math.isfinite(converted) or (result != 0 and converted == 0):
            raise PriceValidationError(f"{label}: Zahl ist im bestehenden Rechenkern nicht darstellbar.")
    return result


def validate_price_fields(value: T, required: tuple[str, ...], optional: tuple[str, ...] = (),
                          *, binary_float: bool = False) -> T:
    """Return a normalized copy, keeping optional None distinct from zero."""
    fields = {}
    for name in required + optional:
        raw = getattr(value, name)
        fields[name] = None if raw is None and name in optional else finite_decimal(
            raw, label=name, binary_float=binary_float,
        )
    return replace(value, **fields)


def validate_period(start: date | None, end: date | None, *, required_start: bool = True,
                    required_end: bool = False) -> None:
    """Validate closed or open date ranges; equality denotes a valid single day."""
    if (start is None and required_start) or (end is None and required_end):
        raise PriceValidationError("Tarifzeitraum: erforderliches Datum fehlt.")
    if any(value is not None and type(value) is not date for value in (start, end)):
        raise PriceValidationError("Tarifzeitraum: gültige Kalenderdaten sind erforderlich.")
    if start is not None and end is not None and end < start:
        raise PriceValidationError("'Gültig bis' darf nicht vor 'Gültig ab' liegen.")
