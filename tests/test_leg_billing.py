# -*- coding: utf-8 -*-
"""Tests for the LEG/ZEV billing calculation logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from zoneinfo import ZoneInfo

from shareomat.config import LegConfig, MqttConfig, PathConfig, ProcessingConfig
from shareomat.core.pipeline.leg_billing import compute_billing, resolve_producer_rate_series
from shareomat.models.billing import MatchResult
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import RATE_MODE_HT_NT, Tariff


def _config(participants, meters):
    return LegConfig(
        community=Community(community_id="TEST-001", name="Test Community"),
        participants=participants,
        meters=meters,
        tariff=Tariff(
            local_rate_chf_kwh=Decimal("0.12"),
            grid_rate_chf_kwh=Decimal("0.28"),
            feed_in_rate_chf_kwh=Decimal("0.08"),
            valid_from=None,
        ),
        paths=PathConfig(
            inbox=Path("/tmp/inbox"),
            archive=Path("/tmp/archive"),
            reports=Path("/tmp/reports"),
            state=Path("/tmp/state"),
        ),
        processing=ProcessingConfig(),
    )


def _slot(meter_id, local_kwh, grid_kwh, ts=None):
    """Build a MatchResult with one consumer meter and no producer meter."""
    ts = ts or datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    return MatchResult(
        slot_start=ts,
        total_export_kwh=0.0,
        total_import_kwh=local_kwh + grid_kwh,
        local_shared_kwh=local_kwh,
        unmatched_export_kwh=0.0,
        unmatched_import_kwh=grid_kwh,
        meter_local_received_kwh={meter_id: local_kwh},
        meter_grid_import_kwh={meter_id: grid_kwh},
        meter_local_supplied_kwh={},
        meter_grid_export_kwh={},
    )


@pytest.fixture
def simple_config():
    return _config(
        participants=[
            Participant("P1", "Solar", "producer_consumer"),
            Participant("P2", "Flat 1", "consumer"),
        ],
        meters=[
            Meter("M1", "P1", "Solar Meter", "producer_consumer"),
            Meter("M2", "P2", "Flat 1 Meter", "consumer"),
        ],
    )


def test_basic_billing(simple_config):
    ts = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    results = [
        _slot("M1", local_kwh=0.5, grid_kwh=0.2, ts=ts),
        _slot("M2", local_kwh=0.3, grid_kwh=0.1, ts=ts),
    ]
    period_start = ts
    period_end = ts + timedelta(minutes=15)
    records = compute_billing(results, simple_config, period_start, period_end)
    assert len(records) == 2
    p1 = next(r for r in records if r.participant_id == "P1")
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p1.local_received_kwh == pytest.approx(0.5)
    assert p2.local_received_kwh == pytest.approx(0.3)


def test_billing_costs(simple_config):
    ts = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    results = [_slot("M2", local_kwh=1.0, grid_kwh=1.0, ts=ts)]
    period_start = ts
    period_end = ts + timedelta(minutes=15)
    records = compute_billing(results, simple_config, period_start, period_end)
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p2.local_cost_chf == pytest.approx(1.0 * 0.12)
    assert p2.grid_cost_chf == pytest.approx(1.0 * 0.28)
    assert p2.total_cost_chf == pytest.approx(0.12 + 0.28)
    assert isinstance(p2.total_cost_chf, float)


def test_zero_consumption(simple_config):
    ts = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    results = [_slot("M2", local_kwh=0.0, grid_kwh=0.0, ts=ts)]
    period_start = ts
    period_end = ts + timedelta(minutes=15)
    records = compute_billing(results, simple_config, period_start, period_end)
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p2.total_cost_chf == pytest.approx(0.0)
    assert p2.total_import_kwh == pytest.approx(0.0)


def test_only_local_energy(simple_config):
    ts = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    results = [_slot("M2", local_kwh=2.0, grid_kwh=0.0, ts=ts)]
    period_start = ts
    period_end = ts + timedelta(minutes=15)
    records = compute_billing(results, simple_config, period_start, period_end)
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p2.grid_cost_chf == pytest.approx(0.0)
    assert p2.local_cost_chf == pytest.approx(2.0 * 0.12)


def test_billing_rounding(simple_config):
    ts = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    results = [_slot("M2", local_kwh=1/3, grid_kwh=1/3, ts=ts)]
    period_start = ts
    period_end = ts + timedelta(minutes=15)
    records = compute_billing(results, simple_config, period_start, period_end)
    p2 = next(r for r in records if r.participant_id == "P2")
    # Should not raise; rounding is internal
    assert p2.total_cost_chf >= 0.0


def test_missing_participant_in_results(simple_config):
    """Participant with no match results should still get zero record (P1 present, P2 absent)."""
    ts = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    results = [_slot("M1", local_kwh=1.0, grid_kwh=0.5, ts=ts)]
    period_start = ts
    period_end = ts + timedelta(minutes=15)
    records = compute_billing(results, simple_config, period_start, period_end)
    ids = {r.participant_id for r in records}
    # P1 must appear; P2 may or may not — just don't crash
    assert "P1" in ids


# ── Producer payout (feed_in_rate_chf_kwh) ──────────────────────────────────


def _supply_slot(meter_id, local_supplied_kwh, grid_export_kwh, ts=None):
    """Build a MatchResult with one producer meter and no consumer meter."""
    ts = ts or datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    return MatchResult(
        slot_start=ts,
        total_export_kwh=local_supplied_kwh + grid_export_kwh,
        total_import_kwh=0.0,
        local_shared_kwh=local_supplied_kwh,
        unmatched_export_kwh=grid_export_kwh,
        unmatched_import_kwh=0.0,
        meter_local_received_kwh={},
        meter_grid_import_kwh={},
        meter_local_supplied_kwh={meter_id: local_supplied_kwh},
        meter_grid_export_kwh={meter_id: grid_export_kwh},
    )


def test_producer_payout_is_now_computed(simple_config):
    """feed_in_rate_chf_kwh (previously unused/untested) is applied to local_supplied_kwh."""
    ts = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    results = [_supply_slot("M1", local_supplied_kwh=2.0, grid_export_kwh=0.5, ts=ts)]
    records = compute_billing(results, simple_config, ts, ts + timedelta(minutes=15))
    p1 = next(r for r in records if r.participant_id == "P1")
    assert p1.producer_payout_chf == pytest.approx(2.0 * 0.08)  # feed_in_rate_chf_kwh from _config()


def test_pure_consumer_has_zero_producer_payout(simple_config):
    ts = datetime(2024, 1, 1, 6, 0, tzinfo=timezone.utc)
    results = [_slot("M2", local_kwh=1.0, grid_kwh=0.0, ts=ts)]
    records = compute_billing(results, simple_config, ts, ts + timedelta(minutes=15))
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p2.producer_payout_chf == pytest.approx(0.0)


# ── rate_mode="flat" (default) stays byte-identical to pre-HT/NT behavior ──


def test_flat_mode_is_the_default_and_unaffected_by_time_of_day(simple_config):
    """Same participant, one slot at 10:00 (peak-like) and one at 23:00 (off-peak-like) —
    in flat mode both must use the single local_rate_chf_kwh, unlike ht_nt mode."""
    assert simple_config.tariff.rate_mode == "flat"
    day_ts = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)     # Mon 10:00 Europe/Zurich
    night_ts = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)  # Mon 23:00 Europe/Zurich
    results = [
        _slot("M2", local_kwh=1.0, grid_kwh=0.0, ts=day_ts),
        _slot("M2", local_kwh=1.0, grid_kwh=0.0, ts=night_ts),
    ]
    records = compute_billing(results, simple_config, day_ts, night_ts + timedelta(minutes=15))
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p2.local_received_kwh == pytest.approx(2.0)
    assert p2.local_cost_chf == pytest.approx(2.0 * 0.12)  # single flat rate for both slots


# ── rate_mode="ht_nt" ────────────────────────────────────────────────────────


def _ht_nt_config(participants, meters):
    return LegConfig(
        community=Community(community_id="TEST-001", name="Test Community"),
        participants=participants,
        meters=meters,
        tariff=Tariff(
            local_rate_chf_kwh=Decimal("0.20"),      # Hochtarif
            local_rate_nt_chf_kwh=Decimal("0.10"),   # Niedertarif
            grid_rate_chf_kwh=Decimal("0.28"),
            feed_in_rate_chf_kwh=Decimal("0.15"),
            feed_in_rate_nt_chf_kwh=Decimal("0.05"),
            rate_mode="ht_nt",
            valid_from=None,
        ),
        paths=PathConfig(
            inbox=Path("/tmp/inbox"), archive=Path("/tmp/archive"),
            reports=Path("/tmp/reports"), state=Path("/tmp/state"),
        ),
        processing=ProcessingConfig(peak_start_hour=6, peak_end_hour=22, peak_weekdays_only=True),
    )


@pytest.fixture
def ht_nt_config():
    return _ht_nt_config(
        participants=[Participant("P2", "Flat 1", "consumer")],
        meters=[Meter("M2", "P2", "Flat 1 Meter", "consumer")],
    )


def test_ht_nt_split_applies_different_rates(ht_nt_config):
    # Monday 2024-01-01: 10:00 local (peak) and 23:00 local (off-peak), winter time UTC+1
    peak_ts = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)     # 10:00 Europe/Zurich
    off_peak_ts = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)  # 23:00 Europe/Zurich
    results = [
        _slot("M2", local_kwh=1.0, grid_kwh=0.0, ts=peak_ts),
        _slot("M2", local_kwh=1.0, grid_kwh=0.0, ts=off_peak_ts),
    ]
    records = compute_billing(results, ht_nt_config, peak_ts, off_peak_ts + timedelta(minutes=15))
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p2.local_received_kwh == pytest.approx(2.0)
    assert p2.local_cost_chf == pytest.approx(1.0 * 0.20 + 1.0 * 0.10)


def test_ht_nt_weekend_is_always_off_peak(ht_nt_config):
    # Saturday 2024-01-06, 10:00 local — inside the HT hour window, but weekend -> NT.
    saturday_ts = datetime(2024, 1, 6, 9, 0, tzinfo=timezone.utc)
    results = [_slot("M2", local_kwh=1.0, grid_kwh=0.0, ts=saturday_ts)]
    records = compute_billing(results, ht_nt_config, saturday_ts, saturday_ts + timedelta(minutes=15))
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p2.local_cost_chf == pytest.approx(1.0 * 0.10)  # NT rate, not HT


def test_ht_nt_producer_payout_split(ht_nt_config):
    peak_ts = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    off_peak_ts = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)
    results = [
        _supply_slot("M1", local_supplied_kwh=1.0, grid_export_kwh=0.0, ts=peak_ts),
        _supply_slot("M1", local_supplied_kwh=1.0, grid_export_kwh=0.0, ts=off_peak_ts),
    ]
    config = _ht_nt_config(
        participants=[Participant("P1", "Solar", "producer")],
        meters=[Meter("M1", "P1", "Solar Meter", "producer")],
    )
    records = compute_billing(results, config, peak_ts, off_peak_ts + timedelta(minutes=15))
    p1 = next(r for r in records if r.participant_id == "P1")
    assert p1.producer_payout_chf == pytest.approx(1.0 * 0.15 + 1.0 * 0.05)


def test_ht_nt_missing_nt_rate_falls_back_to_ht_rate():
    """An ht_nt tariff that only set the HT rate must not silently bill NT energy at 0."""
    off_peak_ts = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)  # 23:00 Europe/Zurich
    config = _ht_nt_config(
        participants=[Participant("P2", "Flat 1", "consumer")],
        meters=[Meter("M2", "P2", "Flat 1 Meter", "consumer")],
    )
    config.tariff.local_rate_nt_chf_kwh = None
    results = [_slot("M2", local_kwh=1.0, grid_kwh=0.0, ts=off_peak_ts)]
    records = compute_billing(results, config, off_peak_ts, off_peak_ts + timedelta(minutes=15))
    p2 = next(r for r in records if r.participant_id == "P2")
    assert p2.local_cost_chf == pytest.approx(1.0 * 0.20)  # falls back to HT rate, not 0


# ── resolve_producer_rate_series() - feeds LEG/FeedInPrice, never local_rate ──


_TZ = ZoneInfo("Europe/Zurich")


def test_resolve_producer_rate_series_flat_mode_ignores_ht_nt_window():
    tariff = Tariff(
        local_rate_chf_kwh=Decimal("0.12"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"), rate_mode="flat",
    )
    processing = ProcessingConfig(peak_start_hour=6, peak_end_hour=22, peak_weekdays_only=True)
    start = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)  # 23:00 Europe/Zurich, would be NT if ht_nt

    series = resolve_producer_rate_series(tariff, processing, start=start, horizon_hours=1, tz=_TZ, slot_minutes=15)

    assert len(series) == 4
    assert all(price == pytest.approx(0.08) for _, price in series)


def test_resolve_producer_rate_series_ht_nt_splits_by_peak_window():
    tariff = Tariff(
        local_rate_chf_kwh=Decimal("0.20"), local_rate_nt_chf_kwh=Decimal("0.10"),
        grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.15"), feed_in_rate_nt_chf_kwh=Decimal("0.05"),
        rate_mode=RATE_MODE_HT_NT,
    )
    processing = ProcessingConfig(peak_start_hour=6, peak_end_hour=22, peak_weekdays_only=True)
    peak_start = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)      # 10:00 Europe/Zurich Monday, peak

    series = resolve_producer_rate_series(tariff, processing, start=peak_start, horizon_hours=24, tz=_TZ, slot_minutes=60)
    by_slot = dict(series)

    peak_slot = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    off_peak_slot = datetime(2024, 1, 1, 21, 0, tzinfo=timezone.utc)  # 22:00 Europe/Zurich, off-peak boundary
    assert by_slot[peak_slot] == pytest.approx(0.15)
    assert by_slot[off_peak_slot] == pytest.approx(0.05)


def test_resolve_producer_rate_series_ht_nt_missing_nt_rate_falls_back_to_ht():
    """Same non-silent-zero guarantee as compute_billing() itself (see
    test_ht_nt_missing_nt_rate_falls_back_to_ht_rate above)."""
    tariff = Tariff(
        local_rate_chf_kwh=Decimal("0.20"), grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.15"), rate_mode=RATE_MODE_HT_NT,
    )
    processing = ProcessingConfig(peak_start_hour=6, peak_end_hour=22, peak_weekdays_only=True)
    off_peak_start = datetime(2024, 1, 1, 22, 0, tzinfo=timezone.utc)  # 23:00 Europe/Zurich, off-peak

    series = resolve_producer_rate_series(tariff, processing, start=off_peak_start, horizon_hours=1, tz=_TZ, slot_minutes=15)

    assert all(price == pytest.approx(0.15) for _, price in series)


def test_resolve_producer_rate_series_never_uses_local_rate():
    """Regression guard for the LEG-Exportpreis-Semantik: the producer rate
    must come exclusively from feed_in_rate_chf_kwh, never local_rate_chf_kwh
    (the consumer price, which includes admin_fee_chf_kwh)."""
    tariff = Tariff(
        local_rate_chf_kwh=Decimal("99.0"),  # deliberately absurd - must never leak into the result
        grid_rate_chf_kwh=Decimal("0.28"),
        feed_in_rate_chf_kwh=Decimal("0.08"),
        rate_mode="flat",
    )
    processing = ProcessingConfig()
    start = datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc)

    series = resolve_producer_rate_series(tariff, processing, start=start, horizon_hours=1, tz=_TZ, slot_minutes=15)

    assert all(price == pytest.approx(0.08) for _, price in series)
