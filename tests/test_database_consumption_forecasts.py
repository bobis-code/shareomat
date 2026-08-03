# -*- coding: utf-8 -*-
"""Tests for consumption forecast persistence (shareomat.database.consumption_forecasts)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from shareomat.database.consumption_forecasts import (
    latest_computed_at,
    list_consumption_forecasts,
    save_consumption_forecast,
)
from shareomat.database.sqlite import init_db
from shareomat.models.forecast import ConsumptionForecastPoint


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    return path


def _point(slot_start, forecast_kwh=1.0, quality="ok", scope="leg", participant_id="") -> ConsumptionForecastPoint:
    return ConsumptionForecastPoint(
        scope=scope, participant_id=participant_id, slot_start=slot_start,
        forecast_kwh=forecast_kwh, quality=quality, method="weekday_time_weighted_recency_v1",
        sample_count=3, data_period_start=slot_start - timedelta(weeks=8), data_period_end=slot_start,
    )


def test_save_and_list(db_path) -> None:
    slot = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
    written = save_consumption_forecast(db_path, [_point(slot)])
    assert written == 1

    points = list_consumption_forecasts(db_path)
    assert len(points) == 1
    assert points[0].forecast_kwh == 1.0
    assert points[0].quality == "ok"


def test_recompute_upserts_instead_of_duplicating(db_path) -> None:
    slot = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
    save_consumption_forecast(db_path, [_point(slot, forecast_kwh=1.0)])
    save_consumption_forecast(db_path, [_point(slot, forecast_kwh=9.9)])  # recomputed later

    points = list_consumption_forecasts(db_path)
    assert len(points) == 1
    assert points[0].forecast_kwh == 9.9  # refreshed, not appended


def test_insufficient_data_point_persists_with_null_forecast(db_path) -> None:
    slot = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
    save_consumption_forecast(db_path, [_point(slot, forecast_kwh=None, quality="insufficient_data")])

    points = list_consumption_forecasts(db_path)
    assert points[0].forecast_kwh is None
    assert points[0].quality == "insufficient_data"


def test_scope_filtering(db_path) -> None:
    slot = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
    save_consumption_forecast(db_path, [_point(slot, scope="leg")])

    assert len(list_consumption_forecasts(db_path, scope="leg")) == 1
    assert len(list_consumption_forecasts(db_path, scope="participant", participant_id="p1")) == 0


def test_latest_computed_at_reflects_most_recent_save(db_path) -> None:
    slot = datetime(2027, 1, 4, 13, 0, tzinfo=timezone.utc)
    assert latest_computed_at(db_path) is None

    save_consumption_forecast(db_path, [_point(slot)])
    first = latest_computed_at(db_path)
    assert first is not None

    save_consumption_forecast(db_path, [_point(slot, forecast_kwh=2.0)])
    second = latest_computed_at(db_path)
    assert second >= first
