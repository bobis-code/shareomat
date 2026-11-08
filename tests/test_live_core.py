# -*- coding: utf-8 -*-
"""Live layer core (ADR-0007): data model, central quality check, sessions, balance, hysteresis.
No broker involved: events are fed straight into the service."""
from __future__ import annotations

import ast
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from shareomat.database.audit import list_events
from shareomat.database.billing_eligibility import EligibilityResolver
from shareomat.database.community import save_community
from shareomat.database.live_sources import (
    LiveSourceError,
    activate_live_source,
    create_live_source,
    get_live_source,
    list_live_sources,
    record_identity_proof,
    retire_live_source,
)
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import connect, init_db
from shareomat.live.adapters.sparkplug import EmsomatLocalAdapter, WanSparkplugAdapter, build_sample
from shareomat.live.balance import compute_balance
from shareomat.live.hysteresis import SurplusDisplay
from shareomat.live.model import (
    COMM_NEVER,
    COMM_OFFLINE,
    COMM_ONLINE,
    DISPLAY_NO_SURPLUS,
    DISPLAY_SURPLUS,
    LIVE_ACTIVE,
    LIVE_NONE,
    LIVE_PLANNED,
    QUALITY_CONFLICT,
    QUALITY_INVALID,
    QUALITY_MISSING,
    QUALITY_OK,
    QUALITY_STALE,
    STATUS_COMPLETE,
    STATUS_NO_DATA,
    STATUS_UNCERTAIN,
    LiveParams,
    MeterBalanceInput,
    RawSample,
    RawValue,
    SessionEnd,
    SessionStart,
    TransportChange,
)
from shareomat.live.service import RATE_MAX_SAMPLES, LiveStatusService
from shareomat.models.community import Community
from shareomat.models.live_source import (
    ADAPTER_EMSOMAT_LOCAL,
    ADAPTER_GATEWAY_MQTT,
    ADAPTER_HA_MQTT,
    ADAPTER_WAN_SPARKPLUG,
    MODE_IMPORT_ONLY,
    MODE_SIGNED,
    SIGN_POSITIVE_IS_EXPORT,
    SIGN_POSITIVE_IS_IMPORT,
    STATE_ACTIVE,
    STATE_RETIRED,
    STATE_TESTING,
)
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.sparkplug.vendor.datatype import DataType
from shareomat.sparkplug.vendor.metric import Metric

NOW = datetime(2026, 11, 8, 12, 0, 0, tzinfo=timezone.utc)
NOW_MS = int(NOW.timestamp() * 1000)
EM = ADAPTER_EMSOMAT_LOCAL
EM_KEY = f"{EM}:EMS-A"
WAN = ADAPTER_WAN_SPARKPLUG
WAN_KEY = f"{WAN}:SITE-B/P2"


class Clock:
    def __init__(self):
        self.now = NOW

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    save_community(path, Community(community_id="LEG-1", name="Test-LEG"))
    create_participant(path, Participant("P1", "Haus 1"))
    create_participant(path, Participant("P2", "Haus 2"))
    create_meter(path, Meter(meter_id="M1", participant_id="P1", label="Haus 1", role="producer_consumer"))
    create_meter(path, Meter(meter_id="M2", participant_id="P2", label="Haus 2", role="consumer"))
    create_meter(path, Meter(meter_id="GRID", participant_id="P1", label="Netz", role="grid"))
    return path


@pytest.fixture
def clock():
    return Clock()


def _service(db_path, clock, **params):
    return LiveStatusService(db_path, LiveParams(**params), clock=clock)


def _emsomat(db_path, *, activate=False, meter="M1"):
    source_id = create_live_source(db_path, meter_id=meter, adapter=EM, edge_node_id="EMS-A", max_power_w=20000)
    if activate:
        activate_live_source(db_path, source_id)
    return source_id


def _wan(db_path, *, activate=False):
    source_id = create_live_source(db_path, meter_id="M2", adapter=WAN, edge_node_id="SITE-B", device_id="P2", max_power_w=20000)
    if activate:
        activate_live_source(db_path, source_id)
    return source_id


def _raw(session="s1", imp=500.0, exp=0.0, ts=NOW_MS, key=EM_KEY, adapter=EM, **overrides):
    values = dict(
        import_value=RawValue(imp, timestamp_ms=ts) if imp is not ... else None,
        export_value=RawValue(exp, timestamp_ms=ts) if exp is not ... else None,
        payload_timestamp_ms=ts,
    )
    received_at = overrides.pop("received_at", NOW)
    values.update(overrides)
    return RawSample(adapter=adapter, source_key=key, session=session, received_at=received_at, **values)


def _feed(service, *events):
    for event in events:
        service.submit(event)
    service.process_pending()


def _reading(service, source_id):
    return service.source_reading(source_id)


def _started(db_path, clock, *, activate=True):
    source_id = _emsomat(db_path, activate=activate)
    service = _service(db_path, clock)
    _feed(service, SessionStart(EM, EM_KEY, "s1"))
    return service, source_id


# ── Data model and "at most one active source" ──────────────────────────────


def test_database_refuses_second_active_source_past_the_code(db_path):
    first = _emsomat(db_path, activate=True)
    other = _wan(db_path)
    with connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE live_sources SET meter_id = (SELECT meter_id FROM live_sources WHERE id = ?), state = 'active' WHERE id = ?",
                     (first, other))


def test_database_refuses_invalid_type_adapter_pair(db_path):
    with connect(db_path) as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO live_sources (meter_id, source_type, adapter, source_key, measurement_mode, max_power_w,
               freshness_evidence, state, created_at, updated_at)
               VALUES (1, 'emsomat', 'wan_sparkplug', 'x', 'import_export', 1, 'C', 'testing', 'n', 'n')""")


def test_source_key_cannot_serve_two_meters(db_path):
    _emsomat(db_path, meter="M1")
    with pytest.raises(LiveSourceError):
        _emsomat(db_path, meter="M2")


def test_retired_key_can_be_assigned_again(db_path):
    first = _emsomat(db_path, meter="M1")
    retire_live_source(db_path, first)
    assert _emsomat(db_path, meter="M2") != first


def test_grid_meter_gets_no_source(db_path):
    with pytest.raises(LiveSourceError):
        create_live_source(db_path, meter_id="GRID", adapter=EM, edge_node_id="EMS-A", max_power_w=1000)


@pytest.mark.parametrize("bad", ["a/b", "a+b", "a#b", "a b", "", "x" * 65, "a\x00b"])
def test_malicious_identifiers_are_refused(db_path, bad):
    with pytest.raises(LiveSourceError):
        create_live_source(db_path, meter_id="M1", adapter=EM, edge_node_id=bad, max_power_w=1000)


@pytest.mark.parametrize("bad", [0, -5, math.nan, math.inf, 2e6, "abc"])
def test_invalid_max_power_is_refused(db_path, bad):
    with pytest.raises(LiveSourceError):
        create_live_source(db_path, meter_id="M1", adapter=EM, edge_node_id="EMS-A", max_power_w=bad)


def test_switch_is_atomic_and_audited(db_path):
    old = _emsomat(db_path, activate=True)
    new = create_live_source(db_path, meter_id="M1", adapter=ADAPTER_GATEWAY_MQTT, device_id="AMS-1", max_power_w=20000)
    assert activate_live_source(db_path, new) == old
    assert get_live_source(db_path, old).state == STATE_RETIRED
    assert get_live_source(db_path, new).state == STATE_ACTIVE
    actions = {e.action for e in list_events(db_path, entity="live_source")}
    assert {"assigned", "activated", "retired"} <= actions


def test_failed_activation_changes_nothing(db_path):
    old = create_live_source(db_path, meter_id="M1", adapter=ADAPTER_HA_MQTT, device_id="ha-1", max_power_w=20000,
                             adapter_config={"import_entity": "sensor.a", "export_entity": "sensor.b"})
    activate_live_source(db_path, old)
    same_channel = create_live_source(db_path, meter_id="M2", adapter=ADAPTER_HA_MQTT, device_id="ha-2", max_power_w=20000,
                                      adapter_config={"import_entity": "sensor.a", "export_entity": "sensor.c"})
    with pytest.raises(LiveSourceError):
        activate_live_source(db_path, same_channel)
    assert get_live_source(db_path, same_channel).state == STATE_TESTING
    assert get_live_source(db_path, old).state == STATE_ACTIVE


def test_two_sites_cannot_share_a_device_id(db_path):
    _wan(db_path)
    with pytest.raises(LiveSourceError):
        create_live_source(db_path, meter_id="M1", adapter=WAN, edge_node_id="SITE-C", device_id="P2", max_power_w=1000)


def test_import_only_needs_a_consumer_meter_and_a_reason(db_path):
    with pytest.raises(LiveSourceError):
        create_live_source(db_path, meter_id="M1", adapter=EM, edge_node_id="EMS-A", max_power_w=1000,
                           measurement_mode=MODE_IMPORT_ONLY, no_export_note="keine PV")
    with pytest.raises(LiveSourceError):
        create_live_source(db_path, meter_id="M2", adapter=EM, edge_node_id="EMS-A", max_power_w=1000,
                           measurement_mode=MODE_IMPORT_ONLY, no_export_note="  ")


def test_signed_needs_a_sign_rule(db_path):
    with pytest.raises(LiveSourceError):
        create_live_source(db_path, meter_id="M1", adapter=ADAPTER_GATEWAY_MQTT, device_id="g", max_power_w=1000,
                           measurement_mode=MODE_SIGNED)


# ── Central check ───────────────────────────────────────────────────────────


def test_valid_values_and_real_zero(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=0.0, exp=0.0))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_OK and reading.import_w == 0.0 and reading.export_w == 0.0


@pytest.mark.parametrize("imp, exp", [(None, 0.0), (0.0, None)])
def test_is_null_is_missing_never_zero(db_path, clock, imp, exp):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=imp, exp=exp))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_MISSING and reading.import_w is None and reading.export_w is None


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf, -1.0, "12", True, 25000.0, [1]])
def test_bad_numbers_are_invalid(db_path, clock, bad):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=bad))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_INVALID and reading.import_w is None


def test_only_one_quantity_in_a_message_is_invalid_and_not_combined(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=500.0, exp=0.0))
    clock.advance(1)
    _feed(service, _raw(imp=700.0, exp=..., ts=NOW_MS + 1000))
    assert _reading(service, source_id).quality == QUALITY_INVALID


def test_different_timestamps_are_invalid(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(import_value=RawValue(1.0, timestamp_ms=NOW_MS), export_value=RawValue(0.0, timestamp_ms=NOW_MS - 5)))
    assert _reading(service, source_id).quality == QUALITY_INVALID


def test_missing_metric_timestamp_uses_payload_timestamp_and_none_at_all_is_invalid(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(import_value=RawValue(1.0), export_value=RawValue(0.0), payload_timestamp_ms=NOW_MS))
    assert _reading(service, source_id).quality == QUALITY_OK
    service2, second = LiveStatusService(db_path, clock=clock), source_id
    _feed(service2, SessionStart(EM, EM_KEY, "s1"), _raw(import_value=RawValue(1.0), export_value=RawValue(0.0), payload_timestamp_ms=None))
    assert _reading(service2, second).quality == QUALITY_INVALID


def test_future_timestamp_is_invalid_within_skew_ok(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(ts=NOW_MS + 20_000))
    assert _reading(service, source_id).quality == QUALITY_OK
    service2 = LiveStatusService(db_path, clock=clock)
    _feed(service2, SessionStart(EM, EM_KEY, "s1"), _raw(ts=NOW_MS + 60_000))
    assert _reading(service2, source_id).quality == QUALITY_INVALID


@pytest.mark.parametrize("ts", [0, -5, 10**15, True, "x"])
def test_absurd_timestamps_are_invalid(db_path, clock, ts):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(import_value=RawValue(1.0, timestamp_ms=ts), export_value=RawValue(0.0, timestamp_ms=ts)))
    assert _reading(service, source_id).quality == QUALITY_INVALID


def test_conflict_when_import_and_export_at_once(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=500.0, exp=500.0))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_CONFLICT and reading.import_w is None


def test_value_goes_stale_after_max_age(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw())
    clock.advance(91)
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_STALE and reading.import_w is None
    assert reading.last_valid_import_w == 500.0


def test_old_message_is_stale_on_arrival(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(ts=NOW_MS - 200_000))
    assert _reading(service, source_id).quality == QUALITY_STALE


def test_repetition_without_new_measurement_does_not_refresh(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw())
    for _ in range(4):
        clock.advance(30)
        _feed(service, _raw())          # same HA timestamp, like Emsomat every 30 s
    assert _reading(service, source_id).quality == QUALITY_STALE


def test_late_message_is_dropped(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=500.0, ts=NOW_MS), _raw(imp=999.0, ts=NOW_MS - 1000))
    reading = _reading(service, source_id)
    assert reading.import_w == 500.0 and reading.dropped == 1


def test_same_time_other_values_is_invalid_until_newer(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=500.0), _raw(imp=600.0))
    assert _reading(service, source_id).quality == QUALITY_INVALID
    _feed(service, _raw(imp=700.0, ts=NOW_MS + 1000))
    assert _reading(service, source_id).import_w == 700.0


def test_null_takes_effect_at_once_and_recovers(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=500.0))
    _feed(service, _raw(imp=None, exp=0.0, ts=NOW_MS))
    assert _reading(service, source_id).quality == QUALITY_MISSING
    _feed(service, _raw(imp=600.0, ts=NOW_MS + 2000))
    assert _reading(service, source_id).import_w == 600.0


def test_retained_message_is_dropped(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(retained=True))
    assert _reading(service, source_id).quality == QUALITY_MISSING


def test_evidence_level_must_be_backed_by_the_source(db_path, clock):
    source_id = create_live_source(db_path, meter_id="M1", adapter=EM, edge_node_id="EMS-A", max_power_w=20000, freshness_evidence="B")
    service = _service(db_path, clock)
    _feed(service, SessionStart(EM, EM_KEY, "s1"), _raw(time_origin="ha_last_reported"))
    assert _reading(service, source_id).quality == QUALITY_INVALID
    _feed(service, _raw(ts=NOW_MS + 1000, time_origin="acquisition", sample_seq=1))
    assert _reading(service, source_id).quality == QUALITY_OK
    _feed(service, _raw(ts=NOW_MS + 2000, time_origin="acquisition", sample_seq=1))   # counter did not rise
    assert _reading(service, source_id).measured_at == NOW + timedelta(seconds=1)


# ── LE-06: meters without a separate export measurement ─────────────────────


def test_import_only_counts_export_as_zero_by_explicit_configuration(db_path, clock):
    source_id = create_live_source(db_path, meter_id="M2", adapter=EM, edge_node_id="EMS-A", max_power_w=20000,
                                   measurement_mode=MODE_IMPORT_ONLY, no_export_note="Keine Erzeugung am Anschluss")
    service = _service(db_path, clock)
    _feed(service, SessionStart(EM, EM_KEY, "s1"), _raw(imp=800.0, exp=None))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_OK and reading.import_w == 800.0 and reading.export_w == 0.0


def test_import_only_rejects_reported_export(db_path, clock):
    source_id = create_live_source(db_path, meter_id="M2", adapter=EM, edge_node_id="EMS-A", max_power_w=20000,
                                   measurement_mode=MODE_IMPORT_ONLY, no_export_note="Keine Erzeugung am Anschluss")
    service = _service(db_path, clock)
    _feed(service, SessionStart(EM, EM_KEY, "s1"), _raw(imp=800.0, exp=2000.0))
    assert _reading(service, source_id).quality == QUALITY_INVALID


def test_missing_export_without_import_only_is_never_zero(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=800.0, exp=None))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_MISSING and reading.export_w is None


@pytest.mark.parametrize("rule, value, imp, exp", [
    (SIGN_POSITIVE_IS_IMPORT, 500.0, 500.0, 0.0),
    (SIGN_POSITIVE_IS_IMPORT, -300.0, 0.0, 300.0),
    (SIGN_POSITIVE_IS_EXPORT, 500.0, 0.0, 500.0),
    (SIGN_POSITIVE_IS_EXPORT, -300.0, 300.0, 0.0),
])
def test_signed_value_uses_the_configured_rule(db_path, clock, rule, value, imp, exp):
    source_id = create_live_source(db_path, meter_id="M1", adapter=ADAPTER_GATEWAY_MQTT, device_id="g1", max_power_w=20000,
                                   measurement_mode=MODE_SIGNED, sign_convention=rule)
    service = _service(db_path, clock)
    key = f"{ADAPTER_GATEWAY_MQTT}:g1"
    _feed(service, SessionStart(ADAPTER_GATEWAY_MQTT, key, "s"),
          RawSample(ADAPTER_GATEWAY_MQTT, key, "s", NOW, signed_value=RawValue(value, timestamp_ms=NOW_MS)))
    reading = _reading(service, source_id)
    assert (reading.import_w, reading.export_w) == (imp, exp)


# ── Sessions, reconnect, identity ───────────────────────────────────────────


def test_sample_without_session_is_dropped(db_path, clock):
    source_id = _emsomat(db_path)
    service = _service(db_path, clock)
    _feed(service, _raw())
    assert _reading(service, source_id).quality == QUALITY_MISSING
    assert _reading(service, source_id).communication == COMM_OFFLINE


def test_never_received_is_visible(db_path, clock):
    source_id = _emsomat(db_path)
    service = _service(db_path, clock)
    assert _reading(service, source_id).communication == COMM_NEVER


def test_new_birth_invalidates_previous_values_and_old_session_is_dropped(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw())
    _feed(service, SessionStart(EM, EM_KEY, "s2"))
    assert _reading(service, source_id).quality == QUALITY_MISSING
    _feed(service, _raw(session="s1", ts=NOW_MS + 1000))
    assert _reading(service, source_id).quality == QUALITY_MISSING
    _feed(service, _raw(session="s2", ts=NOW_MS))     # same timestamp is allowed in a new session
    assert _reading(service, source_id).quality == QUALITY_OK


def test_birth_with_old_values_is_stale(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, SessionStart(EM, EM_KEY, "s2"), _raw(session="s2", ts=NOW_MS - 500_000))
    assert _reading(service, source_id).quality == QUALITY_STALE


def test_death_makes_values_missing_immediately(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(), SessionEnd(EM, EM_KEY))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_MISSING and reading.communication == COMM_OFFLINE


def test_broker_loss_makes_values_missing_immediately_and_needs_a_new_birth(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(), TransportChange(EM, False))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_MISSING and reading.communication == COMM_OFFLINE
    _feed(service, TransportChange(EM, True), _raw(ts=NOW_MS + 1000))
    assert _reading(service, source_id).quality == QUALITY_MISSING
    _feed(service, SessionStart(EM, EM_KEY, "s3"), _raw(session="s3", ts=NOW_MS + 1000))
    reading = _reading(service, source_id)
    assert reading.quality == QUALITY_OK and reading.communication == COMM_ONLINE


def test_unregistered_source_never_gets_in(db_path, clock):
    service = _service(db_path, clock)
    assert service.submit(_raw(key=f"{EM}:FREMD")) is False
    assert service.is_registered_wan("SITE-X", "P9") is False


def test_retired_source_values_are_dropped(db_path, clock):
    service, source_id = _started(db_path, clock)
    retire_live_source(db_path, source_id)
    service.reload_masterdata()
    assert service.submit(_raw()) is False
    assert service.source_reading(source_id) is None


def test_rate_limit_and_queue_overflow_never_raise(db_path, clock):
    service, source_id = _started(db_path, clock)
    results = [service.submit(_raw(ts=NOW_MS + i)) for i in range(RATE_MAX_SAMPLES + 20)]
    assert results.count(True) == RATE_MAX_SAMPLES
    service.process_pending()


def test_queue_overflow_marks_values_missing(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw())
    for _ in range(3000):
        service.submit(TransportChange("other", True))
    service.process_pending()
    assert _reading(service, source_id).quality == QUALITY_MISSING


def _metric(name, value, ts=NOW_MS, is_null=False):
    return Metric(timestamp=ts, name=name, datatype=DataType.DOUBLE, value=value, is_null=is_null)


def test_message_without_power_metrics_means_nothing():
    assert build_sample(EM, EM_KEY, "s", (_metric("LEG/x", 1.0),), NOW_MS, NOW) is None


def test_duplicate_power_metric_is_malformed():
    metrics = (_metric("Market/ImportPowerNow", 1.0), _metric("Market/ImportPowerNow", 2.0), _metric("Market/ExportPowerNow", 0.0))
    assert build_sample(EM, EM_KEY, "s", metrics, NOW_MS, NOW).malformed


def test_too_many_metrics_is_malformed():
    assert build_sample(EM, EM_KEY, "s", tuple(_metric(f"x{i}", 1.0) for i in range(600)), NOW_MS, NOW).malformed


def test_adapter_rawsample_has_no_quality_field():
    assert not hasattr(_raw(), "quality") and not hasattr(_raw(), "base_quality")


# ── Balance ─────────────────────────────────────────────────────────────────


def _input(meter, quality=QUALITY_OK, imp=0.0, exp=1000.0, evidence="A", identity=True, active=True):
    return MeterBalanceInput(meter, quality, "Grund", imp, exp, NOW, evidence, identity, active)


def test_balance_complete():
    balance = compute_balance([_input("M1", exp=3000.0), _input("M2", imp=1000.0, exp=0.0)])
    assert balance.status == STATUS_COMPLETE and balance.surplus_w == 2000.0 and balance.coverage == 1.0


@pytest.mark.parametrize("second", [
    _input("M2", quality=QUALITY_MISSING, imp=None, exp=None),
    _input("M2", quality=QUALITY_STALE, imp=None, exp=None),
    _input("M2", quality=QUALITY_INVALID, imp=None, exp=None),
    _input("M2", quality=QUALITY_CONFLICT, imp=None, exp=None),
    _input("M2", evidence="C"),
    _input("M2", identity=False),
    _input("M2", quality=QUALITY_MISSING, imp=None, exp=None, evidence=None, identity=False, active=False),
])
def test_incomplete_balance_shows_no_surplus(second):
    balance = compute_balance([_input("M1", exp=3000.0), second])
    assert balance.status == STATUS_UNCERTAIN and balance.surplus_w is None and balance.open_points


def test_missing_meter_is_not_counted_as_zero_in_partial_sum():
    balance = compute_balance([_input("M1", exp=3000.0),
                               _input("M2", quality=QUALITY_MISSING, imp=None, exp=None, active=False)])
    assert balance.partial_surplus_w == 3000.0 and balance.coverage == 0.5 and balance.ok_count == 1


def test_no_data_cases():
    assert compute_balance([]).status == STATUS_NO_DATA
    balance = compute_balance([_input("M1", quality=QUALITY_MISSING, imp=None, exp=None)])
    assert balance.status == STATUS_NO_DATA and balance.surplus_w is None


def test_expected_meters_are_exactly_the_billing_meters(db_path, clock):
    service = _service(db_path, clock)
    day = date(2026, 11, 8)
    assert service._expected.meters == EligibilityResolver(db_path).for_day(day).meters
    assert "GRID" not in service._expected.meters


def test_own_value_visible_while_balance_is_incomplete(db_path, clock):
    source_id = _emsomat(db_path, activate=True)
    service = _service(db_path, clock)
    _feed(service, SessionStart(EM, EM_KEY, "s1"), _raw(imp=0.0, exp=2500.0))
    own = service.own_reading()
    status = service.tick()
    assert own.quality == QUALITY_OK and own.export_w == 2500.0
    assert status.status == STATUS_UNCERTAIN and status.surplus_w is None and status.display_kw is None
    assert status.display_text == "Status unsicher" and status.partial_surplus_w == 2500.0
    assert any(meter == "M2" for meter, _ in status.open_points)
    assert source_id


def test_testing_source_is_never_counted(db_path, clock):
    source_id = _emsomat(db_path, activate=False)
    service = _service(db_path, clock)
    _feed(service, SessionStart(EM, EM_KEY, "s1"), _raw(imp=0.0, exp=2500.0))
    status = service.tick()
    assert _reading(service, source_id).quality == QUALITY_OK
    assert status.ok_count == 0 and status.partial_surplus_w is None and service.own_reading().configured is False


def test_complete_balance_reaches_surplus_after_hold(db_path, clock):
    source_id = create_live_source(db_path, meter_id="M1", adapter=EM, edge_node_id="EMS-A", max_power_w=20000, freshness_evidence="B")
    wan_id = create_live_source(db_path, meter_id="M2", adapter=WAN, edge_node_id="SITE-B", device_id="P2", max_power_w=20000, freshness_evidence="B")
    for sid in (source_id, wan_id):
        activate_live_source(db_path, sid)
        record_identity_proof(db_path, sid, note="Negativtest 08.11.2026")
    service = _service(db_path, clock)

    def samples(step):
        ts = NOW_MS + step * 1000
        return (_raw(imp=0.0, exp=2500.0, ts=ts, time_origin="meter", sample_seq=step + 1, received_at=clock.now),
                _raw(imp=500.0, exp=0.0, ts=ts, key=WAN_KEY, adapter=WAN, time_origin="meter", sample_seq=step + 1, received_at=clock.now))

    _feed(service, SessionStart(EM, EM_KEY, "s1"), SessionStart(WAN, WAN_KEY, "s1"), *samples(0))
    status = service.tick()
    assert status.status == STATUS_COMPLETE and status.surplus_w == 2000.0 and status.display_state == DISPLAY_NO_SURPLUS
    assert status.display_text == "kein Überschuss"
    clock.advance(121)
    _feed(service, *samples(121))
    status = service.tick()
    assert status.display_state == DISPLAY_SURPLUS and status.display_kw == 2.0 and status.display_text == "2,0 kW"
    clock.advance(1)
    _feed(service, _raw(imp=None, exp=None, ts=NOW_MS + 122_000))
    status = service.tick()
    assert status.status != STATUS_COMPLETE and status.display_kw is None


def test_meter_rows_show_three_live_states(db_path, clock):
    _emsomat(db_path, activate=True)
    service = _service(db_path, clock)
    rows = {r.meter_id: r for r in service.meter_rows()}
    assert rows["M1"].live_status == LIVE_ACTIVE and rows["M2"].live_status == LIVE_NONE and "GRID" not in rows
    _wan(db_path)
    service.reload_masterdata()
    assert {r.meter_id: r for r in service.meter_rows()}["M2"].live_status == LIVE_PLANNED


def test_rebirth_handler_called_for_new_source(db_path, clock):
    service = _service(db_path, clock)
    calls = []
    service.register_rebirth_handler(EM, lambda: calls.append(1))
    _emsomat(db_path)
    service.reload_masterdata()
    service.reload_masterdata()
    assert calls == [1]


def test_masterdata_failure_shows_no_data(db_path, clock, monkeypatch):
    _emsomat(db_path, activate=True)
    service = _service(db_path, clock)
    import shareomat.live.service as module
    monkeypatch.setattr(module.masterdata, "load_expected", lambda *a: (_ for _ in ()).throw(RuntimeError("kaputt")))
    service.reload_masterdata()
    status = service.tick()
    assert status.status == STATUS_NO_DATA and status.expected_error


def test_last_valid_survives_restart_but_counts_for_nothing(db_path, clock):
    service, source_id = _started(db_path, clock)
    _feed(service, _raw(imp=321.0))
    restarted = _service(db_path, clock)
    reading = _reading(restarted, source_id)
    assert reading.quality == QUALITY_MISSING and reading.import_w is None and reading.last_valid_import_w == 321.0


def test_adapter_flow_session_and_null(db_path, clock):
    from shareomat.sparkplug.vendor.payload import NBirth, NData
    source_id = _emsomat(db_path, activate=True)
    service = _service(db_path, clock)
    adapter = EmsomatLocalAdapter(service, "EMS-A")
    metrics = (_metric("Market/ImportPowerNow", 500.0), _metric("Market/ExportPowerNow", 0.0))
    adapter.on_birth(NBirth(timestamp=NOW_MS, seq=0, metrics=metrics), 7)
    service.process_pending()
    assert _reading(service, source_id).import_w == 500.0
    nulls = (_metric("Market/ImportPowerNow", None, ts=None, is_null=True), _metric("Market/ExportPowerNow", None, ts=None, is_null=True))
    adapter.on_data(NData(timestamp=NOW_MS + 1000, seq=1, metrics=nulls))
    service.process_pending()
    assert _reading(service, source_id).quality == QUALITY_MISSING
    adapter.on_connection(False)
    service.process_pending()
    assert _reading(service, source_id).communication == COMM_OFFLINE


def test_wan_adapter_keeps_sites_apart(db_path, clock):
    from shareomat.sparkplug.vendor.payload import DBirth
    source_id = _wan(db_path)
    service = _service(db_path, clock)
    adapter = WanSparkplugAdapter(service)
    metrics = (_metric("Market/ImportPowerNow", 400.0), _metric("Market/ExportPowerNow", 0.0))
    adapter.on_device_birth("SITE-EVIL", "P2", DBirth(timestamp=NOW_MS, seq=0, metrics=metrics))
    service.process_pending()
    assert _reading(service, source_id).communication == COMM_NEVER
    adapter.on_device_birth("SITE-B", "P2", DBirth(timestamp=NOW_MS, seq=0, metrics=metrics))
    service.process_pending()
    assert _reading(service, source_id).import_w == 400.0


# ── Hysteresis ──────────────────────────────────────────────────────────────


def _display():
    return SurplusDisplay(LiveParams())


def test_hysteresis_needs_stable_surplus():
    d = _display()
    assert d.update(STATUS_COMPLETE, 500.0, NOW) == DISPLAY_NO_SURPLUS
    assert d.update(STATUS_COMPLETE, 200.0, NOW + timedelta(seconds=60)) == DISPLAY_NO_SURPLUS   # resets
    d.update(STATUS_COMPLETE, 500.0, NOW + timedelta(seconds=61))
    assert d.update(STATUS_COMPLETE, 500.0, NOW + timedelta(seconds=180)) == DISPLAY_NO_SURPLUS
    assert d.update(STATUS_COMPLETE, 500.0, NOW + timedelta(seconds=181)) == DISPLAY_SURPLUS


def _on(d):
    d.update(STATUS_COMPLETE, 500.0, NOW)
    d.update(STATUS_COMPLETE, 500.0, NOW + timedelta(seconds=120))
    assert d.state == DISPLAY_SURPLUS


def test_hysteresis_off_after_hold_and_immediately_on_deficit():
    d = _display()
    _on(d)
    assert d.update(STATUS_COMPLETE, 50.0, NOW + timedelta(seconds=130)) == DISPLAY_SURPLUS
    assert d.update(STATUS_COMPLETE, 50.0, NOW + timedelta(seconds=150)) == DISPLAY_NO_SURPLUS
    d2 = _display()
    _on(d2)
    assert d2.update(STATUS_COMPLETE, 0.0, NOW + timedelta(seconds=130)) == DISPLAY_NO_SURPLUS


def test_hysteresis_data_gap_resets():
    d = _display()
    _on(d)
    assert d.update(STATUS_UNCERTAIN, None, NOW + timedelta(seconds=130)) == DISPLAY_NO_SURPLUS


def test_display_rounds_down():
    d = _display()
    _on(d)
    assert d.display_kw(1999.0) == 1.9
    assert d.display_kw(50.0) == 0.0


def test_invalid_parameters_stop_the_live_layer(db_path):
    with pytest.raises(ValueError):
        LiveStatusService(db_path, LiveParams(off_threshold_w=500.0, on_threshold_w=300.0))


# ── Separation from billing ─────────────────────────────────────────────────

ROOT = Path(__file__).resolve().parents[1] / "shareomat"


def _imports(path: Path) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_billing_never_imports_the_live_layer():
    for folder in ("core", "database"):
        for path in (ROOT / folder).rglob("*.py"):
            assert not any(n.startswith("shareomat.live") for n in _imports(path)), path


def test_only_masterdata_reads_the_database_and_only_allowed_modules():
    allowed = {"shareomat.database.billing_eligibility", "shareomat.database.community", "shareomat.database.live_sources",
               "shareomat.database.meters", "shareomat.database.participants"}
    for path in (ROOT / "live").rglob("*.py"):
        used = {n for n in _imports(path) if n.startswith("shareomat.database")}
        if path.name == "masterdata.py":
            assert used <= allowed, used
        else:
            assert not used, (path, used)
        assert not any(n.startswith("shareomat.core") for n in _imports(path)), path


def test_no_home_assistant_api_access_anywhere():
    for path in ROOT.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "SUPERVISOR_TOKEN" not in text and "/api/services" not in text, path


def test_live_values_never_reach_the_billing_database(db_path, clock):
    service, _ = _started(db_path, clock)
    _feed(service, _raw(imp=123.0))
    service.tick()
    with connect(db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM meter_readings").fetchone()["n"] == 0
    assert (db_path.parent / "live.db").exists()


def test_list_excludes_retired_by_default(db_path):
    source_id = _emsomat(db_path)
    retire_live_source(db_path, source_id)
    assert list_live_sources(db_path) == []
    assert len(list_live_sources(db_path, include_retired=True)) == 1
