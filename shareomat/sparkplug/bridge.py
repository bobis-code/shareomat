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
"""

from __future__ import annotations

import logging

from .host import SparkplugHost
from .participant_relay import METRIC_EXPORT_POWER_NOW, METRIC_IMPORT_POWER_NOW, ParticipantRelay

logger = logging.getLogger(__name__)


def mirror_host_to_uplink(host: SparkplugHost, wan_uplink: ParticipantRelay, own_participant_id: str) -> None:
    """Reads the local Emsomat's current ExportPower/ImportPower (and their
    original Metric-level timestamp) from `host` and publishes them to
    `wan_uplink` under `own_participant_id`.

    No-op if Emsomat hasn't sent its NBIRTH/NDATA yet (metrics not known)."""
    export_metric = host.get_metric(METRIC_EXPORT_POWER_NOW)
    import_metric = host.get_metric(METRIC_IMPORT_POWER_NOW)
    if export_metric is None or import_metric is None:
        logger.debug("mirror_host_to_uplink: Emsomat metrics not known yet - skipped")
        return

    export_w = float(export_metric.value or 0.0)
    import_w = float(import_metric.value or 0.0)
    timestamp_ms = export_metric.timestamp

    if own_participant_id in wan_uplink.known_participants:
        wan_uplink.update_participant(own_participant_id, export_w, import_w, timestamp_ms=timestamp_ms)
    else:
        wan_uplink.register_participant(own_participant_id, export_w, import_w, timestamp_ms=timestamp_ms)
