# -*- coding: utf-8 -*-
"""
File: shareomat/live/model.py

Purpose:
    Plain data types of the live layer (ADR-0007, Abschnitt 3 and 4) and the
    single place for all live parameters.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    A RawSample deliberately has no quality field: adapters only translate,
    the quality is decided centrally in shareomat/live/quality.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

QUALITY_OK = "ok"
QUALITY_MISSING = "missing"
QUALITY_INVALID = "invalid"
QUALITY_STALE = "stale"
QUALITY_CONFLICT = "conflict"

STATUS_COMPLETE = "vollstaendig"
STATUS_UNCERTAIN = "unsicher"
STATUS_NO_DATA = "keine_daten"

DISPLAY_SURPLUS = "ueberschuss"
DISPLAY_NO_SURPLUS = "kein_ueberschuss"

COMM_ONLINE = "online"
COMM_OFFLINE = "offline"
COMM_NEVER = "nie_empfangen"

LIVE_NONE = "none"                       # nothing decided yet
LIVE_NO_MEASUREMENT = "no_measurement"   # stored: this meter has no live measurement
LIVE_TESTING = "testing"                 # a technical source is being checked
LIVE_ACTIVE = "active"

TIME_ORIGIN_METER = "meter"
TIME_ORIGIN_ACQUISITION = "acquisition"


@dataclass(frozen=True)
class LiveParams:
    """Reviewable start values (ADR-0007, 3.6). Final values come from real data."""

    max_age_s: float = 90.0            # at least 3x the 30 s Emsomat publish interval
    max_skew_s: float = 30.0
    conflict_w: float = 50.0
    on_threshold_w: float = 300.0
    on_hold_s: float = 120.0
    off_threshold_w: float = 100.0
    off_hold_s: float = 20.0
    display_step_kw: float = 0.1
    eligibility_refresh_s: float = 300.0
    tick_s: float = 5.0

    def validate(self) -> None:
        numbers = (self.max_age_s, self.max_skew_s, self.conflict_w, self.on_threshold_w, self.on_hold_s,
                   self.off_threshold_w, self.off_hold_s, self.display_step_kw, self.eligibility_refresh_s, self.tick_s)
        if any(n < 0 for n in numbers):
            raise ValueError("Live-Parameter dürfen nicht negativ sein.")
        if self.off_threshold_w >= self.on_threshold_w:
            raise ValueError("off_threshold_w muss kleiner als on_threshold_w sein.")
        if self.display_step_kw <= 0 or self.max_age_s <= 0 or self.tick_s <= 0:
            raise ValueError("display_step_kw, max_age_s und tick_s müssen grösser als 0 sein.")


@dataclass(frozen=True)
class RawValue:
    """One quantity as delivered by the transport: untrusted, not interpreted."""

    value: object
    is_null: bool = False
    timestamp_ms: int | None = None
    unit: str = "W"


@dataclass(frozen=True)
class RawSample:
    """One message translated by an adapter. Untrusted input."""

    adapter: str
    source_key: str
    session: str | None
    received_at: datetime
    import_value: RawValue | None = None
    export_value: RawValue | None = None
    signed_value: RawValue | None = None
    payload_timestamp_ms: int | None = None
    time_origin: str | None = None
    sample_seq: int | None = None
    retained: bool = False
    malformed: str | None = None


@dataclass(frozen=True)
class SessionStart:
    adapter: str
    source_key: str
    session: str


@dataclass(frozen=True)
class SessionEnd:
    adapter: str
    source_key: str


@dataclass(frozen=True)
class TransportChange:
    adapter: str
    connected: bool


@dataclass(frozen=True)
class CheckedSample:
    """A sample after the central check. `base_quality` excludes staleness,
    which depends on the moment of reading."""

    base_quality: str
    reason: str
    received_at: datetime
    import_w: float | None = None
    export_w: float | None = None
    measured_at: datetime | None = None
    time_origin: str | None = None
    sample_seq: int | None = None


@dataclass(frozen=True)
class SourceReading:
    """The current view of one source at one moment."""

    quality: str
    reason: str
    communication: str
    import_w: float | None = None
    export_w: float | None = None
    measured_at: datetime | None = None
    received_at: datetime | None = None
    time_origin: str | None = None
    last_valid_at: datetime | None = None
    last_valid_import_w: float | None = None
    last_valid_export_w: float | None = None
    last_message_at: datetime | None = None
    dropped: int = 0


@dataclass(frozen=True)
class MeterBalanceInput:
    """What the balance needs to know about one expected meter."""

    meter_id: str
    quality: str
    reason: str
    import_w: float | None
    export_w: float | None
    measured_at: datetime | None
    evidence: str | None
    identity_verified: bool
    has_active_source: bool


@dataclass(frozen=True)
class Balance:
    status: str
    surplus_w: float | None                # only when complete
    partial_surplus_w: float | None        # admin only: sum over the valid values
    coverage: float
    ok_count: int
    expected_count: int
    oldest_measured_at: datetime | None
    open_points: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class LegStatus:
    """The one source of truth for all outputs (HA sensors, admin page, later the portal)."""

    status: str
    display_state: str
    display_kw: float | None
    display_text: str
    surplus_w: float | None
    partial_surplus_w: float | None
    coverage: float
    ok_count: int
    expected_count: int
    updated_at: datetime
    oldest_measured_at: datetime | None
    community_id: str = ""
    open_points: tuple[tuple[str, str], ...] = ()
    expected_error: str = ""


@dataclass(frozen=True)
class OwnReading:
    """The site's own active Emsomat source, shown independently of the LEG balance."""

    configured: bool
    meter_id: str = ""
    quality: str = QUALITY_MISSING
    reason: str = "Keine aktive Emsomat-Quelle"
    communication: str = COMM_NEVER
    import_w: float | None = None
    export_w: float | None = None
    measured_at: datetime | None = None
    time_origin: str | None = None


@dataclass
class MeterRow:
    """One line of the admin overview."""

    meter_id: str
    label: str
    participant_id: str
    role: str
    in_leg: bool
    excluded_reason: str = ""
    unconfirmed: bool = False
    live_status: str = LIVE_NONE
    active_source: object | None = None
    active_reading: SourceReading | None = None
    testing: list = field(default_factory=list)   # [(LiveSource, SourceReading), ...]
    open_reasons: list = field(default_factory=list)
