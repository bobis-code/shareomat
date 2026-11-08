# -*- coding: utf-8 -*-
"""
File: shareomat/live/balance.py

Purpose:
    The LEG live balance (ADR-0007, 3.4). A meter without a valid live value
    is never counted as 0 W: the balance is then incomplete and shows no
    surplus. A partial sum exists for the administration only.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from shareomat.live.model import (
    QUALITY_OK,
    STATUS_COMPLETE,
    STATUS_NO_DATA,
    STATUS_UNCERTAIN,
    Balance,
    MeterBalanceInput,
)


def _open_point(meter: MeterBalanceInput) -> str | None:
    if not meter.has_active_source:
        return "Keine aktive Live-Messquelle"
    if meter.quality != QUALITY_OK:
        return meter.reason or "Kein gültiger Messwert"
    if meter.evidence not in ("A", "B"):
        return "Nachweisstufe C: Aktualität der Messung nicht belegt"
    if not meter.identity_verified:
        return "Identität der Quelle nicht nachgewiesen"
    return None


def compute_balance(meters: list[MeterBalanceInput]) -> Balance:
    """Balance over the expected meters E(t). Positive surplus = export."""
    expected = len(meters)
    valid = [m for m in meters if m.quality == QUALITY_OK and m.import_w is not None and m.export_w is not None]
    partial = sum(m.export_w - m.import_w for m in valid) if valid else None
    oldest = min((m.measured_at for m in valid if m.measured_at is not None), default=None)
    coverage = (len(valid) / expected) if expected else 0.0
    open_points = tuple((m.meter_id, reason) for m in meters if (reason := _open_point(m)) is not None)

    if expected == 0 or not valid:
        status = STATUS_NO_DATA
    elif open_points:
        status = STATUS_UNCERTAIN
    else:
        status = STATUS_COMPLETE
    return Balance(
        status=status,
        surplus_w=partial if status == STATUS_COMPLETE else None,
        partial_surplus_w=partial,
        coverage=coverage,
        ok_count=len(valid),
        expected_count=expected,
        oldest_measured_at=oldest,
        open_points=open_points,
    )
