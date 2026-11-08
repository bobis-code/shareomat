# -*- coding: utf-8 -*-
"""
File: shareomat/live/quality.py

Purpose:
    The central check every live sample passes, whatever adapter it came
    from (ADR-0007, 3.2, 3.3, 4.1), and the per-source tracking of session,
    order and last valid value.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    All input is untrusted. An invalid or missing value is never turned into
    0 W. Pure logic: no I/O, no threads, the current time is passed in.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from shareomat.live.model import (
    COMM_NEVER,
    COMM_OFFLINE,
    COMM_ONLINE,
    QUALITY_CONFLICT,
    QUALITY_INVALID,
    QUALITY_MISSING,
    QUALITY_OK,
    QUALITY_STALE,
    TIME_ORIGIN_ACQUISITION,
    TIME_ORIGIN_METER,
    CheckedSample,
    LiveParams,
    RawSample,
    RawValue,
    SourceReading,
)
from shareomat.models.live_source import MODE_IMPORT_ONLY, MODE_SIGNED, SIGN_POSITIVE_IS_IMPORT, LiveSource

_UNIT_FACTORS = {"W": 1.0, "kW": 1000.0}
_MIN_TIMESTAMP_MS = 946_684_800_000       # 2000-01-01
_MAX_TIMESTAMP_MS = 4_102_444_800_000     # 2100-01-01


class _Invalid(Exception):
    """Internal: the sample violates a rule; the message is the reason shown."""


class _Missing(Exception):
    """Internal: the source reported the value as unknown."""


def _to_datetime(timestamp_ms: object) -> datetime:
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
        raise _Invalid("Zeitstempel ungültig")
    if not _MIN_TIMESTAMP_MS <= timestamp_ms <= _MAX_TIMESTAMP_MS:
        raise _Invalid("Zeitstempel ausserhalb des gültigen Bereichs")
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)


def _power_w(raw: RawValue, *, allow_negative: bool = False) -> float:
    if isinstance(raw.value, bool) or not isinstance(raw.value, (int, float)):
        raise _Invalid("Wert ist keine Zahl")
    value = float(raw.value)
    if not math.isfinite(value):
        raise _Invalid("Wert ist NaN oder unendlich")
    factor = _UNIT_FACTORS.get(raw.unit)
    if factor is None:
        raise _Invalid("Unbekannte Einheit")
    if value < 0 and not allow_negative:
        raise _Invalid("Negativer Wert")
    return value * factor


def _is_null(raw: RawValue | None) -> bool:
    return raw is not None and (raw.is_null or raw.value is None)


def _common_timestamp_ms(values: list[RawValue], payload_ms: int | None) -> int:
    """Both quantities of one sample have to share one timestamp (4.1 rule 2)."""
    stamps = {v.timestamp_ms for v in values}
    if len(stamps) > 1:
        raise _Invalid("Zeitstempel von Bezug und Einspeisung weichen ab")
    stamp = stamps.pop()
    if stamp is None:
        stamp = payload_ms
    if stamp is None:
        raise _Invalid("Kein Zeitstempel")
    return stamp


def _read_import_export(raw: RawSample, params: LiveParams) -> tuple[float, float, int]:
    if raw.import_value is None or raw.export_value is None:
        raise _Invalid("Bezug und Einspeisung nicht in derselben Nachricht")
    if _is_null(raw.import_value) or _is_null(raw.export_value):
        raise _Missing("Quelle meldet Bezug oder Einspeisung als ungültig")
    values = [raw.import_value, raw.export_value]
    stamp = _common_timestamp_ms(values, raw.payload_timestamp_ms)
    return _power_w(raw.import_value), _power_w(raw.export_value), stamp


def _read_import_only(raw: RawSample, params: LiveParams) -> tuple[float, float, int]:
    if raw.import_value is None:
        raise _Invalid("Bezug fehlt in der Nachricht")
    if _is_null(raw.import_value):
        raise _Missing("Quelle meldet den Bezug als ungültig")
    if raw.export_value is not None and not _is_null(raw.export_value):
        if _power_w(raw.export_value) > params.conflict_w:
            raise _Invalid("Einspeisung gemeldet, obwohl sie als ausgeschlossen hinterlegt ist")
    stamp = _common_timestamp_ms([raw.import_value], raw.payload_timestamp_ms)
    return _power_w(raw.import_value), 0.0, stamp


def _read_signed(raw: RawSample, source: LiveSource) -> tuple[float, float, int]:
    if raw.signed_value is None:
        raise _Invalid("Leistungswert fehlt in der Nachricht")
    if _is_null(raw.signed_value):
        raise _Missing("Quelle meldet den Leistungswert als ungültig")
    if not source.sign_convention:
        raise _Invalid("Vorzeichenregel fehlt")
    value = _power_w(raw.signed_value, allow_negative=True)
    stamp = _common_timestamp_ms([raw.signed_value], raw.payload_timestamp_ms)
    positive_is_import = source.sign_convention == SIGN_POSITIVE_IS_IMPORT
    import_w = max(value, 0.0) if positive_is_import else max(-value, 0.0)
    export_w = max(-value, 0.0) if positive_is_import else max(value, 0.0)
    return import_w, export_w, stamp


def _check_evidence(raw: RawSample, source: LiveSource) -> None:
    """Level A/B are claims of the administrator; the source has to back them
    up in every sample, or the sample is invalid (3.2). Level C needs nothing."""
    if source.freshness_evidence == "C":
        return
    allowed = {"A": {TIME_ORIGIN_METER}, "B": {TIME_ORIGIN_METER, TIME_ORIGIN_ACQUISITION}}[source.freshness_evidence]
    if raw.time_origin not in allowed:
        raise _Invalid(f"Quelle belegt die hinterlegte Nachweisstufe {source.freshness_evidence} nicht")
    if isinstance(raw.sample_seq, bool) or not isinstance(raw.sample_seq, int) or raw.sample_seq < 0:
        raise _Invalid("Erfassungszähler fehlt")


def check_sample(raw: RawSample, source: LiveSource, params: LiveParams, now: datetime) -> CheckedSample:
    """Checks one sample. Staleness is decided when the value is read (reading_quality)."""
    common = {"received_at": raw.received_at, "time_origin": raw.time_origin, "sample_seq": raw.sample_seq}
    try:
        if raw.malformed:
            raise _Invalid(raw.malformed)
        if source.measurement_mode == MODE_SIGNED:
            import_w, export_w, stamp = _read_signed(raw, source)
        elif source.measurement_mode == MODE_IMPORT_ONLY:
            import_w, export_w, stamp = _read_import_only(raw, params)
        else:
            import_w, export_w, stamp = _read_import_export(raw, params)
        measured_at = _to_datetime(stamp)
        if measured_at - now > timedelta(seconds=params.max_skew_s):
            raise _Invalid("Zeitstempel liegt in der Zukunft")
        if import_w > source.max_power_w or export_w > source.max_power_w:
            raise _Invalid("Wert über der Plausibilitätsgrenze")
        _check_evidence(raw, source)
    except _Missing as exc:
        return CheckedSample(QUALITY_MISSING, str(exc), **common)
    except _Invalid as exc:
        return CheckedSample(QUALITY_INVALID, str(exc), **common)
    if import_w > params.conflict_w and export_w > params.conflict_w:
        return CheckedSample(QUALITY_CONFLICT, "Bezug und Einspeisung gleichzeitig", import_w=import_w,
                             export_w=export_w, measured_at=measured_at, **common)
    return CheckedSample(QUALITY_OK, "", import_w=import_w, export_w=export_w, measured_at=measured_at, **common)


def reading_quality(sample: CheckedSample, now: datetime, params: LiveParams) -> tuple[str, str]:
    """The quality of a checked sample at the moment of reading (order per 3.3)."""
    if sample.base_quality in (QUALITY_MISSING, QUALITY_INVALID):
        return sample.base_quality, sample.reason
    limit = timedelta(seconds=params.max_age_s)
    if now - sample.received_at > limit:
        return QUALITY_STALE, "Keine neue Messung empfangen"
    if sample.measured_at is not None and now - sample.measured_at > limit:
        return QUALITY_STALE, "Messwert ist zu alt"
    return sample.base_quality, sample.reason


class SourceTracker:
    """Session, order and last value of one source (ADR-0007, 4.1)."""

    def __init__(self) -> None:
        self.session: str | None = None
        self.sample: CheckedSample | None = None
        self.reason_without_sample = "Noch keine Sitzung der Quelle empfangen"
        self.last_message_at: datetime | None = None
        self.ever_session = False
        self.last_valid: CheckedSample | None = None
        self.dropped = 0
        self._last_measured_at: datetime | None = None
        self._last_payload_ms: int | None = None
        self._last_sample_seq: int | None = None

    def start_session(self, session: str, now: datetime) -> None:
        """A new BIRTH: nothing of the previous session survives."""
        self.session = session
        self.ever_session = True
        self.last_message_at = now
        self.sample = None
        self.reason_without_sample = "Sitzung neu gestartet, noch kein Messwert"
        self._last_measured_at = None
        self._last_payload_ms = None
        self._last_sample_seq = None

    def end_session(self, reason: str) -> None:
        self.session = None
        self.sample = None
        self.reason_without_sample = reason

    def invalidate_samples(self, reason: str) -> None:
        """Values can no longer be trusted, the session itself continues."""
        self.sample = None
        self.reason_without_sample = reason

    def apply(self, raw: RawSample, source: LiveSource, params: LiveParams, now: datetime) -> bool:
        """Takes one sample into account. Returns False if it was dropped."""
        self.last_message_at = now
        if raw.retained or raw.session is None or raw.session != self.session:
            self.dropped += 1
            return False
        checked = check_sample(raw, source, params, now)
        payload_ms = raw.payload_timestamp_ms if isinstance(raw.payload_timestamp_ms, int) else None

        if checked.measured_at is None:
            # A fault notice takes effect at once, unless it is an old message.
            if payload_ms is not None and self._last_payload_ms is not None and payload_ms < self._last_payload_ms:
                self.dropped += 1
                return False
        else:
            if self._last_measured_at is not None:
                if checked.measured_at < self._last_measured_at:
                    self.dropped += 1
                    return False
                if checked.measured_at == self._last_measured_at:
                    previous = self.sample
                    if previous is None or previous.measured_at is None or (
                        previous.import_w == checked.import_w and previous.export_w == checked.export_w
                    ):
                        self.dropped += 1          # a repetition proves nothing new
                        return False
                    self.sample = CheckedSample(QUALITY_INVALID, "Gleicher Messzeitpunkt mit anderen Werten",
                                                received_at=checked.received_at)
                    return True
            if source.freshness_evidence != "C":
                if self._last_sample_seq is not None and checked.sample_seq <= self._last_sample_seq:
                    self.dropped += 1
                    return False
                self._last_sample_seq = checked.sample_seq
            self._last_measured_at = checked.measured_at

        if payload_ms is not None:
            self._last_payload_ms = payload_ms
        self.sample = checked
        if checked.base_quality == QUALITY_OK:
            self.last_valid = checked
        return True

    def reading(self, now: datetime, params: LiveParams, *, transport_up: bool | None) -> SourceReading:
        last_valid = self.last_valid
        if transport_up is False:
            communication = COMM_OFFLINE
        elif self.session is not None:
            communication = COMM_ONLINE
        elif self.ever_session or self.last_message_at is not None:
            communication = COMM_OFFLINE
        else:
            communication = COMM_NEVER
        common = dict(
            communication=communication,
            last_valid_at=last_valid.measured_at if last_valid else None,
            last_valid_import_w=last_valid.import_w if last_valid else None,
            last_valid_export_w=last_valid.export_w if last_valid else None,
            last_message_at=self.last_message_at,
            dropped=self.dropped,
        )
        sample = self.sample
        if sample is None:
            return SourceReading(QUALITY_MISSING, self.reason_without_sample, **common)
        quality, reason = reading_quality(sample, now, params)
        return SourceReading(
            quality, reason,
            import_w=sample.import_w if quality == QUALITY_OK else None,
            export_w=sample.export_w if quality == QUALITY_OK else None,
            measured_at=sample.measured_at,
            received_at=sample.received_at,
            time_origin=sample.time_origin,
            **common,
        )
