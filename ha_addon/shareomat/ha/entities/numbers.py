# -*- coding: utf-8 -*-
"""
File: shareomat/ha/entities/numbers.py

Purpose:
    Number entity definitions, published under the Shareomat Engine device.
    Add a new number input by adding one line to NUMBERS.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NumberDef:
    """One number input — the value set in HA is published as plain text to command_topic_suffix."""

    uid_suffix: str
    name: str
    command_topic_suffix: str
    unit: str
    min_value: float
    max_value: float
    step: float
    icon: str | None = None


# The former "demand_test" dev/test-aid entry (published to the now-removed
# plain-MQTT energy_data/demand_forecast/test_set topic) was removed with
# the Sparkplug Hard Cut (ADR-0004, Emsomat_Shareomat_MQTT_Vertrag.md
# Abschnitt 30.6) - no replacement built, no Number entity needed it.
NUMBERS: list[NumberDef] = []
