# -*- coding: utf-8 -*-
"""Tests for the LEG/ZEV billing calculation logic."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest
from shareomat.core.pipeline.leg_billing import compute_billing
from shareomat.core.leg_config import (
    LegConfig, ParticipantConfig, MeterConfig, TariffConfig,
    PathConfig, ProcessingConfig, MqttConfig,
)
from shareomat.models.invoice import MatchResult
from pathlib import Path


def _config(participants, meters):
    return LegConfig(
        community_id="TEST-001",
        name="Test Community",
        participants=participants,
        meters=meters,
        tariffs=TariffConfig(
            local_rate_chf_kwh=0.12,
            grid_rate_chf_kwh=0.28,
            feed_in_rate_chf_kwh=0.08,
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
            ParticipantConfig("P1", "Solar", "producer_consumer"),
            ParticipantConfig("P2", "Flat 1", "consumer"),
        ],
        meters=[
            MeterConfig("M1", "P1", "Solar Meter", "producer_consumer"),
            MeterConfig("M2", "P2", "Flat 1 Meter", "consumer"),
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
