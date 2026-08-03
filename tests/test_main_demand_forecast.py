# -*- coding: utf-8 -*-
"""Tests for main.py's demand-forecast recompute throttle (_maybe_recompute_demand_forecast)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import main as app_main
from shareomat.database.consumption_forecasts import latest_computed_at
from shareomat.database.sqlite import init_db


def test_first_call_computes_and_persists_a_forecast(tmp_path) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    assert latest_computed_at(db_path) is None

    app_main._maybe_recompute_demand_forecast(db_path)

    assert latest_computed_at(db_path) is not None


def test_second_call_within_throttle_window_does_not_recompute(tmp_path) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)

    app_main._maybe_recompute_demand_forecast(db_path)
    first = latest_computed_at(db_path)

    app_main._maybe_recompute_demand_forecast(db_path)
    second = latest_computed_at(db_path)

    assert first == second  # no write happened on the second, immediate call


def test_stale_forecast_triggers_recompute(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    old_time = datetime.now(timezone.utc) - timedelta(hours=1)
    monkeypatch.setattr(app_main, "latest_computed_at", lambda db_path, scope="leg": old_time)

    calls = []
    monkeypatch.setattr(app_main, "save_consumption_forecast", lambda db_path, points: calls.append(points))

    app_main._maybe_recompute_demand_forecast(db_path)

    assert len(calls) == 1  # recomputed because the stored forecast is older than the throttle window


def test_fresh_forecast_skips_recompute(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "shareomat.db"
    init_db(db_path)
    recent_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    monkeypatch.setattr(app_main, "latest_computed_at", lambda db_path, scope="leg": recent_time)

    calls = []
    monkeypatch.setattr(app_main, "save_consumption_forecast", lambda db_path, points: calls.append(points))

    app_main._maybe_recompute_demand_forecast(db_path)

    assert calls == []  # last forecast is well within the throttle window
