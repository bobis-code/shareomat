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


# Dev/test aid: lets a developer manually inject a demand_forecast value from
# the HA GUI to exercise a real consumer's (e.g. Emsomat's) ingestion path
# without waiting for enough accumulated meter data for a real forecast —
# see mqtt_runtime.publish_manual_demand_test().
NUMBERS: list[NumberDef] = [
    NumberDef("demand_test", "Shareomat Test-Bedarf (LEG)", "energy_data/demand_forecast/test_set",
              "kWh", 0, 50, 0.1, icon="mdi:test-tube"),
]
