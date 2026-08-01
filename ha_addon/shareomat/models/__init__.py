# -*- coding: utf-8 -*-
"""
File: shareomat/models/__init__.py

Purpose:
    Domain model package for the Shareomat engine.
    Re-exports all model classes for convenient imports.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from shareomat.models.community import Community
from shareomat.models.participant import Participant
from shareomat.models.meter import Meter
from shareomat.models.meter_data import IntervalReading, EnergySlot, ImportFile
from shareomat.models.tariff import Tariff
from shareomat.models.settings import OperationSettings
from shareomat.models.billing import BillingRecord, MatchResult

__all__ = [
    "Community",
    "Participant",
    "Meter",
    "IntervalReading",
    "EnergySlot",
    "ImportFile",
    "Tariff",
    "OperationSettings",
    "BillingRecord",
    "MatchResult",
]
