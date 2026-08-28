# -*- coding: utf-8 -*-
"""Tests for shareomat/sparkplug/coordinator_mapping.py (Shareomat's sending
side of LEG/DemandForecast, LEG/ExportPrice, LEG/FeedInPrice)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from shareomat.models.external_data import DayAheadPricePoint
from shareomat.models.forecast import ConsumptionForecastPoint
from shareomat.sparkplug import coordinator_mapping
from shareomat.sparkplug.vendor.payload import NCmd


def _demand_point(slot_start, forecast_kwh, quality="ok", sample_count=5):
    return ConsumptionForecastPoint(
        scope="leg", participant_id="", slot_start=slot_start, forecast_kwh=forecast_kwh,
        quality=quality, method="weekday_time_weighted_recency_v1", sample_count=sample_count,
        data_period_start=None, data_period_end=None, computed_at=None,
    )


def test_demand_forecast_to_metrics_encodes_dataset_and_envelope():
    points = [
        _demand_point(datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc), 1.5),
        _demand_point(datetime(2026, 8, 28, 12, 15, tzinfo=timezone.utc), None, quality="insufficient_data", sample_count=1),
    ]
    metrics = coordinator_mapping.demand_forecast_to_metrics(
        points, timestamp_ms=1000, created_at_iso="2026-08-28T12:00:00+00:00",
        valid_until_iso="2026-08-28T18:00:00+00:00", quality="ok", method="weekday_time_weighted_recency_v1",
    )
    by_name = {m.name: m for m in metrics}
    dataset = by_name[coordinator_mapping.METRIC_DEMAND_FORECAST].value
    assert len(dataset.rows) == 2
    assert dataset.rows[0][1] == pytest.approx(1.5)
    assert by_name[coordinator_mapping.METRIC_DEMAND_FORECAST_QUALITY].value == "ok"
    assert by_name[coordinator_mapping.METRIC_DEMAND_FORECAST_METHOD].value == "weekday_time_weighted_recency_v1"

    # roundtrip through raw NCMD bytes, same as the real transport
    ncmd = NCmd(timestamp=1000, metrics=metrics)
    decoded = NCmd.decode(ncmd.encode(include_dtypes=True))
    assert len(decoded.metrics) == len(metrics)


def test_export_price_to_metrics_marks_empty_series_insufficient():
    metrics = coordinator_mapping.export_price_to_metrics(
        [], timestamp_ms=1000, created_at_iso="2026-08-28T12:00:00+00:00", valid_until_iso="2026-08-28T18:00:00+00:00",
    )
    by_name = {m.name: m for m in metrics}
    assert by_name[coordinator_mapping.METRIC_EXPORT_PRICE_QUALITY].value == "insufficient_data"
    assert by_name[coordinator_mapping.METRIC_EXPORT_PRICE].value.rows == ()


def test_export_price_to_metrics_encodes_native_resolution_points():
    """Regression guard fuer die Granularitaets-Korrektur: export_price_to_metrics()
    darf keine Tagesaggregation vornehmen, sondern gibt jeden nativen
    Preispunkt (z.B. stuendlich) als eigene Zeile weiter."""
    points = [
        DayAheadPricePoint(slot_start=datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc), price_chf_kwh=Decimal("0.2345")),
        DayAheadPricePoint(slot_start=datetime(2026, 8, 28, 13, 0, tzinfo=timezone.utc), price_chf_kwh=Decimal("0.1999")),
    ]
    metrics = coordinator_mapping.export_price_to_metrics(
        points, timestamp_ms=1000, created_at_iso="2026-08-28T12:00:00+00:00", valid_until_iso="2026-08-28T18:00:00+00:00",
    )
    by_name = {m.name: m for m in metrics}
    dataset = by_name[coordinator_mapping.METRIC_EXPORT_PRICE].value
    assert len(dataset.rows) == 2  # zwei native Stundenwerte, nicht zu einem Tageswert verschmolzen
    assert dataset.rows[0][1] == pytest.approx(0.2345)
    assert dataset.rows[1][1] == pytest.approx(0.1999)
    assert by_name[coordinator_mapping.METRIC_EXPORT_PRICE_QUALITY].value == "ok"


def test_feed_in_price_to_metrics_encodes_resolved_series():
    series = [
        (datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc), 0.15),
        (datetime(2026, 8, 28, 12, 15, tzinfo=timezone.utc), 0.05),
    ]
    metrics = coordinator_mapping.feed_in_price_to_metrics(
        series, timestamp_ms=1000, created_at_iso="2026-08-28T12:00:00+00:00", valid_until_iso="2026-08-28T18:00:00+00:00",
    )
    by_name = {m.name: m for m in metrics}
    dataset = by_name[coordinator_mapping.METRIC_FEED_IN_PRICE].value
    assert [row[1] for row in dataset.rows] == [pytest.approx(0.15), pytest.approx(0.05)]
    assert by_name[coordinator_mapping.METRIC_FEED_IN_PRICE_QUALITY].value == "ok"


def test_feed_in_price_metrics_are_distinct_from_export_price_metrics():
    """Regression guard: the two price metrics must never share a name -
    otherwise Emsomat's decoder (leg_mapping.py) would merge them."""
    export_names = {
        m.name for m in coordinator_mapping.export_price_to_metrics(
            [], timestamp_ms=1000, created_at_iso="x", valid_until_iso="y",
        )
    }
    feed_in_names = {
        m.name for m in coordinator_mapping.feed_in_price_to_metrics(
            [], timestamp_ms=1000, created_at_iso="x", valid_until_iso="y",
        )
    }
    assert export_names.isdisjoint(feed_in_names)
