# -*- coding: utf-8 -*-
"""
File: shareomat/models/live_source.py

Purpose:
    Domain model for one live measurement source: a technical data source
    assigned to exactly one official meter (ADR-0007, Abschnitt 2).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container. An official meter stays the billing identity; a
    live source only adds a live data connection to it. Import and export
    are two quantities of ONE source, not two sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

SOURCE_TYPE_EMSOMAT = "emsomat"
SOURCE_TYPE_HOME_ASSISTANT = "home_assistant"
SOURCE_TYPE_EXTERNAL = "external"

ADAPTER_EMSOMAT_LOCAL = "emsomat_local"
ADAPTER_HA_MQTT = "ha_mqtt"
ADAPTER_WAN_SPARKPLUG = "wan_sparkplug"
ADAPTER_GATEWAY_MQTT = "gateway_mqtt"

# adapter -> the source type the administrator chooses for it
ADAPTER_SOURCE_TYPES = {
    ADAPTER_EMSOMAT_LOCAL: SOURCE_TYPE_EMSOMAT,
    ADAPTER_HA_MQTT: SOURCE_TYPE_HOME_ASSISTANT,
    ADAPTER_WAN_SPARKPLUG: SOURCE_TYPE_EXTERNAL,
    ADAPTER_GATEWAY_MQTT: SOURCE_TYPE_EXTERNAL,
}

MODE_IMPORT_EXPORT = "import_export"
MODE_SIGNED = "signed"
MODE_IMPORT_ONLY = "import_only"

SIGN_POSITIVE_IS_IMPORT = "positive_is_import"
SIGN_POSITIVE_IS_EXPORT = "positive_is_export"

EVIDENCE_LEVELS = ("A", "B", "C")

STATE_TESTING = "testing"
STATE_ACTIVE = "active"
STATE_RETIRED = "retired"

# A meter without a technical live source: stored, and never read as 0 W.
METER_STATE_NO_MEASUREMENT = "no_measurement"
METER_STATES = (METER_STATE_NO_MEASUREMENT,)

IDENTITY_BROKER_ACL = "broker_acl"


@dataclass
class LiveSource:
    """One registered live source. `meter_id`/`participant_id` are resolved
    from the master data on read and never stored twice."""

    id: int
    meter_row_id: int
    meter_id: str
    participant_id: str
    source_type: str
    adapter: str
    source_key: str
    measurement_mode: str
    max_power_w: float
    freshness_evidence: str
    state: str
    sign_convention: str | None = None
    no_export_note: str = ""
    adapter_config: dict = field(default_factory=dict)
    identity_basis: str = ""
    identity_note: str = ""
    identity_verified_at: datetime | None = None
    activated_at: datetime | None = None
    retired_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def identity_verified(self) -> bool:
        return bool(self.identity_basis) and self.identity_verified_at is not None
