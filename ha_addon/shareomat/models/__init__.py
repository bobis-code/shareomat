# -*- coding: utf-8 -*-
"""
File: shareomat/models/__init__.py

Purpose:
    Domain model package for the Shareomat engine.
    Re-exports all model classes for backwards compatibility.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from shareomat.models.meter import IntervalReading, EnergySlot, ImportFile
from shareomat.models.invoice import BillingRecord, MatchResult

__all__ = [
    "IntervalReading",
    "EnergySlot",
    "ImportFile",
    "BillingRecord",
    "MatchResult",
]
