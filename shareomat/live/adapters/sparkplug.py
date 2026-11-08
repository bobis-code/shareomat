# -*- coding: utf-8 -*-
"""
File: shareomat/live/adapters/sparkplug.py

Purpose:
    Adapters for the two existing Sparkplug paths: the local Emsomat
    (SparkplugHost) and other sites over the WAN (WanDownlink). They turn
    decoded messages into RawSample and session events (ADR-0007, 4.1).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    One sample comes from exactly one message: import and export are never
    combined across messages. A message with neither power metric (e.g. a
    forecast NDATA) means nothing here and is ignored.
"""

from __future__ import annotations

from collections import Counter

from shareomat.live.model import RawSample, RawValue, SessionEnd, SessionStart, TransportChange
from shareomat.models.live_source import ADAPTER_EMSOMAT_LOCAL, ADAPTER_WAN_SPARKPLUG

METRIC_IMPORT = "Market/ImportPowerNow"
METRIC_EXPORT = "Market/ExportPowerNow"
METRIC_TIME_SOURCE = "Market/PowerTimeSource"
METRIC_SAMPLE_SEQ = "Market/PowerSampleSeq"

MAX_METRICS = 512
_MAX_TIME_SOURCE_LEN = 32
_POWER_METRICS = (METRIC_IMPORT, METRIC_EXPORT)


def _raw_value(metric) -> RawValue | None:
    if metric is None:
        return None
    timestamp = metric.timestamp if isinstance(metric.timestamp, int) else None
    return RawValue(value=metric.value, is_null=bool(metric.is_null), timestamp_ms=timestamp)


def build_sample(adapter: str, source_key: str, session: str | None, metrics, payload_timestamp, received_at) -> RawSample | None:
    """A RawSample from one message, or None if it carries no power metric."""
    if len(metrics) > MAX_METRICS:
        return RawSample(adapter, source_key, session, received_at, malformed="Zu viele Metrics in der Nachricht")
    counts = Counter(m.name for m in metrics if m.name in _POWER_METRICS)
    if not counts:
        return None
    if any(n > 1 for n in counts.values()):
        return RawSample(adapter, source_key, session, received_at, malformed="Leistungswert mehrfach in der Nachricht")
    by_name = {m.name: m for m in metrics if m.name}

    time_origin = None
    source_metric = by_name.get(METRIC_TIME_SOURCE)
    if source_metric is not None and not source_metric.is_null and isinstance(source_metric.value, str):
        if len(source_metric.value) <= _MAX_TIME_SOURCE_LEN:
            time_origin = source_metric.value
    sample_seq = None
    seq_metric = by_name.get(METRIC_SAMPLE_SEQ)
    if seq_metric is not None and not seq_metric.is_null and isinstance(seq_metric.value, int):
        sample_seq = seq_metric.value

    return RawSample(
        adapter=adapter,
        source_key=source_key,
        session=session,
        received_at=received_at,
        import_value=_raw_value(by_name.get(METRIC_IMPORT)),
        export_value=_raw_value(by_name.get(METRIC_EXPORT)),
        payload_timestamp_ms=payload_timestamp if isinstance(payload_timestamp, int) else None,
        time_origin=time_origin,
        sample_seq=sample_seq,
    )


class EmsomatLocalAdapter:
    """Live sink of SparkplugHost. The local Emsomat node is fixed by configuration."""

    def __init__(self, service, edge_node_id: str) -> None:
        self._service = service
        self._key = f"{ADAPTER_EMSOMAT_LOCAL}:{edge_node_id}"
        self._session: str | None = None
        self._births = 0

    def on_connection(self, connected: bool) -> None:
        if not connected:
            self._session = None
        self._service.submit(TransportChange(ADAPTER_EMSOMAT_LOCAL, connected))

    def on_birth(self, birth, bd_seq) -> None:
        self._births += 1
        self._session = f"{bd_seq}.{self._births}"
        self._service.submit(SessionStart(ADAPTER_EMSOMAT_LOCAL, self._key, self._session))
        self._submit(birth)

    def on_data(self, data) -> None:
        if self._session is not None:
            self._submit(data)

    def on_death(self, bd_seq) -> None:
        self._session = None
        self._service.submit(SessionEnd(ADAPTER_EMSOMAT_LOCAL, self._key))

    def _submit(self, message) -> None:
        sample = build_sample(ADAPTER_EMSOMAT_LOCAL, self._key, self._session, message.metrics,
                              message.timestamp, self._service.now())
        if sample is not None:
            self._service.submit(sample)


class WanSparkplugAdapter:
    """Live sink of WanDownlink. Only registered pairs get here; the service checks again."""

    def __init__(self, service) -> None:
        self._service = service
        self._sessions: dict[str, str] = {}
        self._births = 0

    @staticmethod
    def _key(edge_node_id: str, device_id: str) -> str:
        return f"{ADAPTER_WAN_SPARKPLUG}:{edge_node_id}/{device_id}"

    def on_connection(self, connected: bool) -> None:
        if not connected:
            self._sessions.clear()
        self._service.submit(TransportChange(ADAPTER_WAN_SPARKPLUG, connected))

    def on_device_birth(self, edge_node_id: str, device_id: str, birth) -> None:
        key = self._key(edge_node_id, device_id)
        self._births += 1
        session = f"dbirth.{self._births}"
        self._sessions[key] = session
        self._service.submit(SessionStart(ADAPTER_WAN_SPARKPLUG, key, session))
        self._submit(key, session, birth)

    def on_device_data(self, edge_node_id: str, device_id: str, data) -> None:
        key = self._key(edge_node_id, device_id)
        session = self._sessions.get(key)
        if session is not None:
            self._submit(key, session, data)

    def on_device_death(self, edge_node_id: str, device_id: str) -> None:
        key = self._key(edge_node_id, device_id)
        self._sessions.pop(key, None)
        self._service.submit(SessionEnd(ADAPTER_WAN_SPARKPLUG, key))

    def _submit(self, key: str, session: str, message) -> None:
        sample = build_sample(ADAPTER_WAN_SPARKPLUG, key, session, message.metrics, message.timestamp, self._service.now())
        if sample is not None:
            self._service.submit(sample)
