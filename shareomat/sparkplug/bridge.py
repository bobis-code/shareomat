# -*- coding: utf-8 -*-
"""
File: shareomat/sparkplug/bridge.py

Bridges the local Emsomat's own participant state (received via
SparkplugHost, see Emsomat_Shareomat_MQTT_Vertrag.md) into the WAN Uplink
(see Shareomat_CrossHouse_Sparkplug_Vertrag.md Abschnitt 2.1/3). This is the
ONLY source for the WAN Uplink's own participant data - it must never read
from WanDownlink or the local ParticipantRelay's remote-device registry
(that would defeat Origin/Loop Prevention).

Deliberately a plain function, not a new class/store: SparkplugHost already
owns the state (`get_metric()`), ParticipantRelay (returned by
build_wan_uplink()) already owns the publish mechanism
(`register_participant()`/`update_participant()`) - this just wires the two
existing, tested components together.

An invalid Emsomat value stays invalid on the way out: is_null is passed on
and never becomes a real 0 W (ADR-0007 S-L1, K-3).
"""

from __future__ import annotations

import logging
import math
from typing import Optional

from .host import SparkplugHost
from .participant_relay import (
    METRIC_EXPORT_POWER_NOW,
    METRIC_IMPORT_POWER_NOW,
    METRIC_POWER_TIME_SOURCE,
    ParticipantRelay,
)
from .vendor.metric import Metric

logger = logging.getLogger(__name__)

_MAX_TIME_SOURCE_LEN = 32


def _power_w(metric: Metric) -> Optional[float]:
    """A valid power reading in W, or None. A value without a timestamp is
    treated as invalid: forwarding it would stamp it with the current time."""
    if metric.is_null or metric.value is None or metric.timestamp is None or isinstance(metric.value, bool):
        return None
    try:
        value = float(metric.value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _time_source(metric: Optional[Metric]) -> Optional[str]:
    if metric is None or metric.is_null or not isinstance(metric.value, str):
        return None
    value = metric.value.strip()
    return value if value and len(value) <= _MAX_TIME_SOURCE_LEN else None


def mirror_host_to_uplink(host: SparkplugHost, wan_uplink: ParticipantRelay, own_participant_id: str) -> None:
    """Reads the local Emsomat's current ExportPower/ImportPower (and their
    original Metric-level timestamp) from `host` and publishes them to
    `wan_uplink` under `own_participant_id`.

    If Emsomat hasn't sent its NBIRTH/NDATA yet this is a no-op; if it went
    away (NDEATH, broker lost) the participant is removed on the WAN (DDEATH)
    instead of leaving its last value standing."""
    export_metric = host.get_metric(METRIC_EXPORT_POWER_NOW)
    import_metric = host.get_metric(METRIC_IMPORT_POWER_NOW)
    if export_metric is None or import_metric is None:
        if own_participant_id in wan_uplink.known_participants:
            wan_uplink.deregister_participant(own_participant_id)
        logger.debug("mirror_host_to_uplink: Emsomat metrics not known - nothing to mirror")
        return

    export_w = _power_w(export_metric)
    import_w = _power_w(import_metric)
    timestamp_ms = export_metric.timestamp if export_w is not None else import_metric.timestamp
    time_source = _time_source(host.get_metric(METRIC_POWER_TIME_SOURCE))

    if own_participant_id in wan_uplink.known_participants:
        wan_uplink.update_participant(own_participant_id, export_w, import_w, timestamp_ms=timestamp_ms, time_source=time_source)
    else:
        wan_uplink.register_participant(own_participant_id, export_w, import_w, timestamp_ms=timestamp_ms, time_source=time_source)
    wan_uplink.republish_births_if_due()
