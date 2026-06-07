# -*- coding: utf-8 -*-
"""
File: self_leg/models/__init__.py

Purpose:
    Domain model package for the SELF LEG engine.
    Re-exports all model classes for backwards compatibility.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine
"""

from self_leg.models.meter import IntervalReading, EnergySlot, ImportFile
from self_leg.models.invoice import BillingRecord, MatchResult

__all__ = [
    "IntervalReading",
    "EnergySlot",
    "ImportFile",
    "BillingRecord",
    "MatchResult",
]
