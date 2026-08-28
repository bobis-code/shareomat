# -*- coding: utf-8 -*-
"""
File: shareomat/sparkplug/coordinator_mapping.py

Maps Shareomat's own Coordinator-/Market-domain values (LEG demand
forecast, day-ahead export price, LEG producer feed-in credit) onto the
Sparkplug Metrics sent to Emsomat via NCMD, per
docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md Abschnitt 9/10 and
ADR-0004. Counterpart of Emsomat/sparkplug/leg_mapping.py, which decodes
these same metric names on the receiving side - metric names and DataSet
column layouts must stay in sync between the two modules.

Pure mapping functions only - no MQTT/paho knowledge (that lives in
shareomat/sparkplug/host.py::SparkplugHost.publish_coordinator_ncmd()), no
domain computation (the values themselves come from
shareomat.core.pipeline.leg_billing/consumption_forecast and
shareomat.database.*, assembled by the caller in main.py).

Three independent Coordinator values, each carrying its own envelope
(CreatedAt/ValidUntil/Source/Quality) because each has its own freshness
and quality semantics - never merged into one combined envelope:

- LEG/DemandForecast  - LEG-wide consumption forecast (15-min slots).
- LEG/ExportPrice     - normal day-ahead market/export price curve (feeds
                        Emsomat's canonical `export_price` /
                        manager.get_export_price() when
                        leg.price_source == "shareomat"). Precizes the
                        former unspecific 'LEG/Price' placeholder from the
                        Vertrag draft.
- LEG/FeedInPrice     - producer feed-in credit for locally-sold energy
                        (feed_in_rate_chf_kwh / _nt, HT/NT already
                        resolved server-side, see
                        leg_billing.resolve_producer_rate_series()). Never
                        local_rate_chf_kwh, which is the consumer price
                        including admin_fee_chf_kwh.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

from shareomat.models.external_data import DayAheadPricePoint
from shareomat.models.forecast import ConsumptionForecastPoint

from .vendor.dataset import Column, Dataset
from .vendor.datatype import DataType
from .vendor.metric import Metric

METRIC_DEMAND_FORECAST = "LEG/DemandForecast"
METRIC_DEMAND_FORECAST_CREATED_AT = "LEG/DemandForecast/CreatedAt"
METRIC_DEMAND_FORECAST_VALID_UNTIL = "LEG/DemandForecast/ValidUntil"
METRIC_DEMAND_FORECAST_SOURCE = "LEG/DemandForecast/Source"
METRIC_DEMAND_FORECAST_QUALITY = "LEG/DemandForecast/Quality"
METRIC_DEMAND_FORECAST_SCOPE = "LEG/DemandForecast/Scope"
METRIC_DEMAND_FORECAST_METHOD = "LEG/DemandForecast/Method"

METRIC_EXPORT_PRICE = "LEG/ExportPrice"
METRIC_EXPORT_PRICE_CREATED_AT = "LEG/ExportPrice/CreatedAt"
METRIC_EXPORT_PRICE_VALID_UNTIL = "LEG/ExportPrice/ValidUntil"
METRIC_EXPORT_PRICE_SOURCE = "LEG/ExportPrice/Source"
METRIC_EXPORT_PRICE_QUALITY = "LEG/ExportPrice/Quality"

METRIC_FEED_IN_PRICE = "LEG/FeedInPrice"
METRIC_FEED_IN_PRICE_CREATED_AT = "LEG/FeedInPrice/CreatedAt"
METRIC_FEED_IN_PRICE_VALID_UNTIL = "LEG/FeedInPrice/ValidUntil"
METRIC_FEED_IN_PRICE_SOURCE = "LEG/FeedInPrice/Source"
METRIC_FEED_IN_PRICE_QUALITY = "LEG/FeedInPrice/Quality"

_DEMAND_FORECAST_COLUMNS = (
    Column("slot_start", DataType.STRING),
    Column("forecast_kwh", DataType.DOUBLE),
    Column("quality", DataType.STRING),
    Column("sample_count", DataType.INT64),
)

_PRICE_COLUMNS = (
    Column("slot_start", DataType.STRING),
    Column("price_chf_kwh", DataType.DOUBLE),
    Column("quality", DataType.STRING),
)


def demand_forecast_to_metrics(
    points: Iterable[ConsumptionForecastPoint],
    *,
    timestamp_ms: int,
    created_at_iso: str,
    valid_until_iso: str,
    quality: str,
    method: str,
    scope: str = "leg",
    source: str = "shareomat",
) -> tuple[Metric, ...]:
    """LEG demand forecast -> Sparkplug Metrics for NCMD. Replaces the
    removed {prefix}/energy_data/demand_forecast plain-JSON payload
    (docs/emsomat-integration.md, now historical) with the same envelope
    semantics over Sparkplug."""
    rows = tuple(
        (
            p.slot_start.isoformat(),
            p.forecast_kwh if p.forecast_kwh is not None else float("nan"),
            p.quality,
            int(p.sample_count),
        )
        for p in points
    )
    dataset = Dataset(columns=_DEMAND_FORECAST_COLUMNS, rows=rows)
    return (
        Metric(timestamp=timestamp_ms, name=METRIC_DEMAND_FORECAST, datatype=DataType.DATASET, value=dataset),
        Metric(timestamp=timestamp_ms, name=METRIC_DEMAND_FORECAST_CREATED_AT, datatype=DataType.STRING, value=created_at_iso),
        Metric(timestamp=timestamp_ms, name=METRIC_DEMAND_FORECAST_VALID_UNTIL, datatype=DataType.STRING, value=valid_until_iso),
        Metric(timestamp=timestamp_ms, name=METRIC_DEMAND_FORECAST_SOURCE, datatype=DataType.STRING, value=source),
        Metric(timestamp=timestamp_ms, name=METRIC_DEMAND_FORECAST_QUALITY, datatype=DataType.STRING, value=quality),
        Metric(timestamp=timestamp_ms, name=METRIC_DEMAND_FORECAST_SCOPE, datatype=DataType.STRING, value=scope),
        Metric(timestamp=timestamp_ms, name=METRIC_DEMAND_FORECAST_METHOD, datatype=DataType.STRING, value=method or ""),
    )


def export_price_to_metrics(
    points: Iterable[DayAheadPricePoint],
    *,
    timestamp_ms: int,
    created_at_iso: str,
    valid_until_iso: str,
    source: str = "shareomat",
) -> tuple[Metric, ...]:
    """Day-ahead market/export price curve -> Sparkplug Metrics for NCMD.
    Feeds Emsomat's canonical `export_price` (manager.get_export_price())
    when leg.price_source == "shareomat". `points` must be at ENTSO-E's
    NATIVE resolution (see core.pipeline.export_price_forecast) - never
    aggregated to one daily value, since Emsomat's slot lookup (LegPriceForecast.
    slot_for(), "last known value at/before ts") only handles a coarser-than-
    expected resolution correctly, never a finer one it doesn't have. An
    empty `points` list still produces a valid (empty) DataSet with
    quality="insufficient_data" - Emsomat treats that as "no data", never
    as a price of 0."""
    rows = tuple(
        (p.slot_start.isoformat(), float(p.price_chf_kwh), "ok")
        for p in points
    )
    dataset = Dataset(columns=_PRICE_COLUMNS, rows=rows)
    overall_quality = "ok" if rows else "insufficient_data"
    return (
        Metric(timestamp=timestamp_ms, name=METRIC_EXPORT_PRICE, datatype=DataType.DATASET, value=dataset),
        Metric(timestamp=timestamp_ms, name=METRIC_EXPORT_PRICE_CREATED_AT, datatype=DataType.STRING, value=created_at_iso),
        Metric(timestamp=timestamp_ms, name=METRIC_EXPORT_PRICE_VALID_UNTIL, datatype=DataType.STRING, value=valid_until_iso),
        Metric(timestamp=timestamp_ms, name=METRIC_EXPORT_PRICE_SOURCE, datatype=DataType.STRING, value=source),
        Metric(timestamp=timestamp_ms, name=METRIC_EXPORT_PRICE_QUALITY, datatype=DataType.STRING, value=overall_quality),
    )


def feed_in_price_to_metrics(
    series: Iterable[tuple[datetime, float]],
    *,
    timestamp_ms: int,
    created_at_iso: str,
    valid_until_iso: str,
    source: str = "shareomat",
) -> tuple[Metric, ...]:
    """Resolved LEG producer feed-in credit curve (HT/NT already applied,
    see leg_billing.resolve_producer_rate_series()) -> Sparkplug Metrics
    for NCMD. This is the producer payout rate (feed_in_rate_chf_kwh),
    never local_rate_chf_kwh (the consumer price, includes
    admin_fee_chf_kwh - see docs/decisions, LEG-Exportpreis-Klaerung)."""
    rows = tuple((ts.isoformat(), float(price), "ok") for ts, price in series)
    dataset = Dataset(columns=_PRICE_COLUMNS, rows=rows)
    overall_quality = "ok" if rows else "insufficient_data"
    return (
        Metric(timestamp=timestamp_ms, name=METRIC_FEED_IN_PRICE, datatype=DataType.DATASET, value=dataset),
        Metric(timestamp=timestamp_ms, name=METRIC_FEED_IN_PRICE_CREATED_AT, datatype=DataType.STRING, value=created_at_iso),
        Metric(timestamp=timestamp_ms, name=METRIC_FEED_IN_PRICE_VALID_UNTIL, datatype=DataType.STRING, value=valid_until_iso),
        Metric(timestamp=timestamp_ms, name=METRIC_FEED_IN_PRICE_SOURCE, datatype=DataType.STRING, value=source),
        Metric(timestamp=timestamp_ms, name=METRIC_FEED_IN_PRICE_QUALITY, datatype=DataType.STRING, value=overall_quality),
    )
