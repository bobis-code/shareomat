# -*- coding: utf-8 -*-
"""
File: shareomat/live/service.py

Purpose:
    LiveStatusService: takes events from the adapters, keeps the current
    state per live source and is the single source for every output
    (HA sensors, admin page, later the portal) - ADR-0007, I1.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    submit() is called from MQTT callbacks: it never blocks and never raises
    (bounded queue, rate limit). If the queue overflows, values are marked
    missing rather than silently going stale. The worker thread is optional:
    tests and the demo drive process_pending()/tick() directly, so the whole
    layer can be used without a broker.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from shareomat.live import masterdata
from shareomat.live.balance import compute_balance
from shareomat.live.hysteresis import SurplusDisplay
from shareomat.live.model import (
    COMM_NEVER,
    DISPLAY_NO_SURPLUS,
    DISPLAY_SURPLUS,
    LIVE_ACTIVE,
    LIVE_NONE,
    LIVE_PLANNED,
    QUALITY_MISSING,
    QUALITY_OK,
    STATUS_COMPLETE,
    STATUS_NO_DATA,
    STATUS_UNCERTAIN,
    Balance,
    CheckedSample,
    LegStatus,
    LiveParams,
    MeterBalanceInput,
    MeterRow,
    OwnReading,
    RawSample,
    SessionEnd,
    SessionStart,
    SourceReading,
    TransportChange,
)
from shareomat.live.quality import SourceTracker
from shareomat.live.store import LiveStore, live_db_path_for
from shareomat.models.live_source import ADAPTER_EMSOMAT_LOCAL, ADAPTER_WAN_SPARKPLUG, STATE_ACTIVE, STATE_TESTING, LiveSource

logger = logging.getLogger(__name__)

QUEUE_SIZE = 2048
RATE_WINDOW_S = 10.0
RATE_MAX_SAMPLES = 40        # per source and window
_MAX_RATE_KEYS = 512
_PERSIST_INTERVAL_S = 5.0
_SOURCES_REFRESH_S = 30.0
_PRUNE_INTERVAL_S = 3600.0
RECENT_VALID_S = 300.0       # activation needs a valid sample at most this old


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LiveStatusService:
    def __init__(
        self,
        db_path: Path,
        params: LiveParams | None = None,
        *,
        clock: Callable[[], datetime] = utcnow,
        store: LiveStore | None = None,
    ) -> None:
        self.params = params or LiveParams()
        self.params.validate()
        self._db_path = db_path
        self._clock = clock
        self._store = store or LiveStore(live_db_path_for(db_path))
        self._lock = threading.RLock()
        self._queue: "queue.Queue[object]" = queue.Queue(maxsize=QUEUE_SIZE)
        self._overflowed = threading.Event()
        self._rate: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

        self._sources: dict[int, LiveSource] = {}
        self._by_key: dict[tuple[str, str], LiveSource] = {}
        self._sources_ok = False
        self._sources_loaded_at = 0.0
        self._trackers: dict[int, SourceTracker] = {}
        self._transport: dict[str, bool] = {}
        self._rebirth_handlers: dict[str, Callable[[], None]] = {}
        self._listeners: list[Callable[["LiveStatusService", LegStatus], None]] = []
        self._unknown_pairs_getter: Callable[[], tuple] | None = None

        self._expected: masterdata.ExpectedMeters | None = None
        self._expected_loaded_at: datetime | None = None
        self._expected_error = ""
        self._community_id = ""
        self._display = SurplusDisplay(self.params)
        self._status: LegStatus | None = None
        self._last_persist: dict[int, float] = {}
        self._restored: dict[int, dict] = {}
        self._last_prune = 0.0
        self._last_event_key: tuple[str, str] | None = None

        self._safely("init store", self._store.init)
        restored = self._safely("load latest", self._store.load_latest)
        self._restored = restored or {}
        self.reload_masterdata()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="shareomat-live", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _run(self) -> None:
        next_tick = 0.0
        while not self._stop.is_set():
            try:
                self.process_pending(timeout=0.5)
                if time.monotonic() >= next_tick:
                    self.tick()
                    next_tick = time.monotonic() + self.params.tick_s
            except Exception:
                logger.exception("Live service cycle failed")

    def _safely(self, what: str, fn: Callable, *args):
        try:
            return fn(*args)
        except Exception as exc:
            logger.warning("Live store: %s failed: %s", what, exc)
            return None

    # ------------------------------------------------------------------
    # wiring
    # ------------------------------------------------------------------
    def now(self) -> datetime:
        return self._clock()

    def register_rebirth_handler(self, adapter: str, handler: Callable[[], None]) -> None:
        """Called when a new source of this adapter was registered: it needs a BIRTH to start."""
        self._rebirth_handlers[adapter] = handler

    def add_listener(self, listener: Callable[["LiveStatusService", LegStatus], None]) -> None:
        self._listeners.append(listener)

    def register_unknown_pairs_getter(self, getter: Callable[[], tuple]) -> None:
        self._unknown_pairs_getter = getter

    def unknown_wan_pairs(self) -> tuple:
        getter = self._unknown_pairs_getter
        return tuple(getter()) if getter else ()

    def is_registered(self, adapter: str, source_key: str) -> bool:
        """Thread-safe read used by transports to drop unregistered sources early."""
        return (adapter, source_key) in self._by_key

    def is_registered_wan(self, edge_node_id: str, device_id: str) -> bool:
        return self.is_registered(ADAPTER_WAN_SPARKPLUG, f"{ADAPTER_WAN_SPARKPLUG}:{edge_node_id}/{device_id}")

    # ------------------------------------------------------------------
    # input - never blocks, never raises
    # ------------------------------------------------------------------
    def _rate_allows(self, key: tuple[str, str], now_mono: float) -> bool:
        history = self._rate.get(key)
        if history is None:
            history = []
            self._rate[key] = history
            while len(self._rate) > _MAX_RATE_KEYS:
                self._rate.popitem(last=False)
        history[:] = [t for t in history if now_mono - t < RATE_WINDOW_S]
        if len(history) >= RATE_MAX_SAMPLES:
            return False
        history.append(now_mono)
        return True

    def submit(self, event: object) -> bool:
        try:
            if isinstance(event, RawSample):
                key = (event.adapter, event.source_key)
                if key not in self._by_key:
                    return False
                if not self._rate_allows(key, time.monotonic()):
                    return False
            self._queue.put_nowait(event)
            return True
        except queue.Full:
            self._overflowed.set()
            return False
        except Exception:
            logger.debug("Live submit failed", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # processing
    # ------------------------------------------------------------------
    def process_pending(self, timeout: float = 0.0) -> int:
        """Handles all queued events. Worker thread, or directly in tests."""
        count = 0
        block = timeout > 0
        while True:
            try:
                event = self._queue.get(block=block, timeout=timeout if block else None)
            except queue.Empty:
                break
            block = False
            count += 1
            with self._lock:
                self._handle(event)
        if self._overflowed.is_set():
            self._overflowed.clear()
            with self._lock:
                for tracker in self._trackers.values():
                    tracker.invalidate_samples("Eingangswarteschlange übergelaufen, Messwerte verworfen")
        return count

    def _tracker(self, source: LiveSource) -> SourceTracker:
        tracker = self._trackers.get(source.id)
        if tracker is None:
            tracker = SourceTracker()
            restored = self._restored.get(source.id)
            if restored is not None:
                tracker.last_valid = CheckedSample(
                    QUALITY_OK, "", received_at=restored["received_at"], import_w=restored["import_w"],
                    export_w=restored["export_w"], measured_at=restored["measured_at"], time_origin=restored["time_origin"],
                )
            self._trackers[source.id] = tracker
        return tracker

    def _handle(self, event: object) -> None:
        now = self._clock()
        if isinstance(event, TransportChange):
            self._transport[event.adapter] = event.connected
            if not event.connected:
                for source in self._sources.values():
                    if source.adapter == event.adapter:
                        self._tracker(source).end_session("Verbindung zum Broker unterbrochen")
            return
        source = self._by_key.get((event.adapter, event.source_key))
        if source is None:
            return
        tracker = self._tracker(source)
        if isinstance(event, SessionStart):
            tracker.start_session(event.session, now)
        elif isinstance(event, SessionEnd):
            tracker.end_session("Quelle hat die Sitzung beendet (offline)")
        elif isinstance(event, RawSample):
            if tracker.apply(event, source, self.params, now):
                self._persist(source, tracker)

    def _persist(self, source: LiveSource, tracker: SourceTracker) -> None:
        sample = tracker.sample
        if sample is None or sample.base_quality != QUALITY_OK or sample.measured_at is None:
            return
        now_mono = time.monotonic()
        if now_mono - self._last_persist.get(source.id, -1e9) < _PERSIST_INTERVAL_S:
            return
        self._last_persist[source.id] = now_mono
        self._safely("save latest", self._store.save_latest, source.id, self._community_id, sample.import_w, sample.export_w,
                     sample.measured_at, sample.received_at, sample.time_origin)

    # ------------------------------------------------------------------
    # master data
    # ------------------------------------------------------------------
    def reload_masterdata(self) -> None:
        """Re-reads sources and E(t). Called after every admin change."""
        with self._lock:
            self._reload_sources()
            self._reload_expected(self._clock())

    def _reload_sources(self) -> None:
        previous = set(self._sources)
        try:
            sources = masterdata.load_sources(self._db_path)
            self._community_id = masterdata.community_id(self._db_path)
        except Exception as exc:
            logger.warning("Live: could not read live sources: %s", exc)
            self._sources, self._by_key = {}, {}
            self._sources_ok = False
            return
        self._sources = {s.id: s for s in sources}
        self._by_key = {(s.adapter, s.source_key): s for s in sources}
        self._sources_ok = True
        self._sources_loaded_at = time.monotonic()
        new_adapters = {s.adapter for s in sources if s.id not in previous}
        for adapter in new_adapters:
            handler = self._rebirth_handlers.get(adapter)
            if handler is not None:
                try:
                    handler()
                except Exception:
                    logger.debug("Live: rebirth handler failed", exc_info=True)

    def _reload_expected(self, now: datetime) -> None:
        try:
            self._expected = masterdata.load_expected(self._db_path, now)
            self._expected_error = ""
        except Exception as exc:
            logger.warning("Live: could not determine the LEG meters: %s", exc)
            self._expected = None
            self._expected_error = "Die LEG-Messpunkte konnten nicht ermittelt werden."
        self._expected_loaded_at = now

    def _refresh_if_due(self, now: datetime) -> None:
        if time.monotonic() - self._sources_loaded_at >= _SOURCES_REFRESH_S:
            self._reload_sources()
        loaded = self._expected_loaded_at
        if (
            self._expected is None or loaded is None
            or masterdata.local_day(now) != self._expected.day
            or (now - loaded).total_seconds() >= self.params.eligibility_refresh_s
        ):
            self._reload_expected(now)

    # ------------------------------------------------------------------
    # reading
    # ------------------------------------------------------------------
    def _reading(self, source: LiveSource, now: datetime) -> SourceReading:
        return self._tracker(source).reading(now, self.params, transport_up=self._transport.get(source.adapter))

    def _active_by_meter(self) -> dict[str, list[LiveSource]]:
        result: dict[str, list[LiveSource]] = {}
        for source in self._sources.values():
            if source.state == STATE_ACTIVE:
                result.setdefault(source.meter_id, []).append(source)
        return result

    def _balance_inputs(self, now: datetime) -> list[MeterBalanceInput]:
        active = self._active_by_meter()
        inputs: list[MeterBalanceInput] = []
        for meter_id in (self._expected.meters if self._expected else {}):
            sources = active.get(meter_id, [])
            if len(sources) > 1:      # the database forbids this; never sum regardless
                inputs.append(MeterBalanceInput(meter_id, "invalid", "Mehrere aktive Quellen", None, None, None, None, False, True))
                continue
            if not sources:
                inputs.append(MeterBalanceInput(meter_id, QUALITY_MISSING, "Keine aktive Live-Messquelle", None, None, None, None, False, False))
                continue
            source = sources[0]
            reading = self._reading(source, now)
            inputs.append(MeterBalanceInput(meter_id, reading.quality, reading.reason, reading.import_w, reading.export_w,
                                            reading.measured_at, source.freshness_evidence, source.identity_verified, True))
        return inputs

    def _compute_status(self, now: datetime) -> LegStatus:
        if not self._sources_ok or self._expected is None:
            balance = Balance(STATUS_NO_DATA, None, None, 0.0, 0, len(self._expected.meters) if self._expected else 0, None)
        else:
            balance = compute_balance(self._balance_inputs(now))
        display = self._display.update(balance.status, balance.surplus_w, now)
        display_kw = self._display.display_kw(balance.surplus_w)
        if balance.status == STATUS_COMPLETE:
            if display == DISPLAY_SURPLUS:
                text = "< 0,1 kW" if not display_kw else f"{display_kw:.1f} kW".replace(".", ",")
            else:
                text = "kein Überschuss"
        elif balance.status == STATUS_UNCERTAIN:
            text = "Status unsicher"
        else:
            text = "keine Live-Daten"
        return LegStatus(
            status=balance.status,
            display_state=display if balance.status == STATUS_COMPLETE else DISPLAY_NO_SURPLUS,
            display_kw=display_kw,
            display_text=text,
            surplus_w=balance.surplus_w,
            partial_surplus_w=balance.partial_surplus_w,
            coverage=balance.coverage,
            ok_count=balance.ok_count,
            expected_count=balance.expected_count,
            updated_at=now,
            oldest_measured_at=balance.oldest_measured_at,
            community_id=self._community_id,
            open_points=balance.open_points,
            expected_error=self._expected_error,
        )

    def tick(self) -> LegStatus:
        """Recomputes the status. The hysteresis needs this at a regular pace."""
        now = self._clock()
        with self._lock:
            self._refresh_if_due(now)
            status = self._compute_status(now)
            self._status = status
            self._log_transition(status, now)
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(self, status)
            except Exception:
                logger.warning("Live status listener failed", exc_info=True)
        return status

    def _log_transition(self, status: LegStatus, now: datetime) -> None:
        key = (status.status, status.display_state)
        if key != self._last_event_key:
            self._last_event_key = key
            self._safely("add event", self._store.add_event, self._community_id, "status",
                         f"{status.status} / {status.display_state} ({status.ok_count}/{status.expected_count})", now)
        if time.monotonic() - self._last_prune >= _PRUNE_INTERVAL_S:
            self._last_prune = time.monotonic()
            self._safely("prune", self._store.prune, now)

    def leg_status(self) -> LegStatus:
        """The last computed status (computed once if tick() has not run yet)."""
        with self._lock:
            if self._status is None:
                self._status = self._compute_status(self._clock())
            return self._status

    def own_reading(self, *, now: datetime | None = None) -> OwnReading:
        """The active Emsomat source: valid values are shown even if the balance is incomplete."""
        moment = now or self._clock()
        with self._lock:
            for source in self._sources.values():
                if source.state == STATE_ACTIVE and source.adapter == ADAPTER_EMSOMAT_LOCAL:
                    reading = self._reading(source, moment)
                    return OwnReading(True, source.meter_id, reading.quality, reading.reason, reading.communication,
                                      reading.import_w, reading.export_w, reading.measured_at, reading.time_origin)
        return OwnReading(False)

    def source_reading(self, source_id: int, *, now: datetime | None = None) -> SourceReading | None:
        with self._lock:
            source = self._sources.get(source_id)
            return self._reading(source, now or self._clock()) if source else None

    def can_activate(self, source_id: int, *, now: datetime | None = None) -> tuple[bool, str]:
        """A source is activated only after it delivered a valid value recently (ADR-0007, 5.1)."""
        moment = now or self._clock()
        with self._lock:
            source = self._sources.get(source_id)
            if source is None:
                return False, "Die Live-Messquelle wurde nicht gefunden."
            if source.state != STATE_TESTING:
                return False, "Nur eine Live-Messquelle in Prüfung kann aktiviert werden."
            reading = self._reading(source, moment)
        if reading.quality == QUALITY_OK:
            return True, ""
        if reading.last_valid_at is not None and (moment - reading.last_valid_at) <= timedelta(seconds=RECENT_VALID_S):
            return True, ""
        if reading.communication == COMM_NEVER:
            return False, "Von dieser Quelle wurde noch nichts empfangen."
        return False, f"Kein gültiger Messwert: {reading.reason}"

    def meter_rows(self, *, now: datetime | None = None) -> list[MeterRow]:
        """One row per meter for the admin page, LEG members first."""
        moment = now or self._clock()
        with self._lock:
            try:
                meters = masterdata.load_meters(self._db_path)
            except Exception as exc:
                logger.warning("Live: could not read meters: %s", exc)
                return []
            expected = self._expected
            unconfirmed = expected.unconfirmed_participants if expected else set()
            status_points = dict(self._status.open_points) if self._status else {}
            rows: list[MeterRow] = []
            for meter in meters:
                in_leg = bool(expected and meter.meter_id in expected.meters)
                row = MeterRow(
                    meter_id=meter.meter_id, label=meter.label, participant_id=meter.participant_id, role=meter.role,
                    in_leg=in_leg,
                    excluded_reason=(expected.excluded.get(meter.meter_id, "") if expected else "") or ("" if in_leg else "Heute nicht Teil der LEG"),
                    unconfirmed=meter.participant_id in unconfirmed,
                )
                for source in self._sources.values():
                    if source.meter_id != meter.meter_id:
                        continue
                    if source.state == STATE_ACTIVE:
                        row.active_source = source
                        row.active_reading = self._reading(source, moment)
                    elif source.state == STATE_TESTING:
                        row.planned.append((source, self._reading(source, moment)))
                row.live_status = LIVE_ACTIVE if row.active_source else (LIVE_PLANNED if row.planned else LIVE_NONE)
                if meter.meter_id in status_points:
                    row.open_reasons.append(status_points[meter.meter_id])
                rows.append(row)
        rows.sort(key=lambda r: (not r.in_leg, r.label))
        return rows

    def recent_events(self, limit: int = 20) -> list[dict]:
        return self._safely("recent events", self._store.recent_events, limit) or []
