# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/metric.py

Vendored from pysparkplug._metric (Apache 2.0), see NOTICE.md.
"""

import dataclasses
from typing import Optional, Union

from .dataset import Dataset
from .datatype import DataType
from .metadata import Metadata
from .protobuf_types import Metric as PB_Metric
from .types import MetricValue, Self

__all__ = ["Metric"]


@dataclasses.dataclass(frozen=True)
class Metric:
    """Class representing a Sparkplug B metric"""

    timestamp: Optional[int]
    name: Optional[str]
    datatype: DataType
    metadata: Optional[Metadata] = None
    value: Optional[Union[MetricValue, Dataset]] = None
    alias: Optional[int] = None
    is_historical: bool = False
    is_transient: bool = False
    is_null: bool = False

    def to_pb(self, include_dtype: bool) -> PB_Metric:
        metric = PB_Metric()
        if self.timestamp is not None:
            metric.timestamp = self.timestamp
        if self.name is not None:
            metric.name = self.name
        if include_dtype:
            metric.datatype = self.datatype
        if self.metadata is not None:
            metric.metadata.CopyFrom(self.metadata.to_pb())
        if self.alias is not None:
            metric.alias = self.alias
        if self.is_historical:
            metric.is_historical = self.is_historical
        if self.is_transient:
            metric.is_transient = self.is_transient
        if self.is_null or self.value is None:
            metric.is_null = True
        elif self.datatype == DataType.DATASET:
            # message-typed value - needs CopyFrom(), not setattr()
            metric.dataset_value.CopyFrom(self.value.to_pb())
        else:
            setattr(metric, self.datatype.field, self.datatype.encode(self.value))

        return metric

    @classmethod
    def from_pb(cls, metric: PB_Metric) -> Self:
        datatype = DataType(metric.datatype)
        value_field = metric.WhichOneof("value")
        if value_field == "dataset_value":
            value = Dataset.from_pb(metric.dataset_value)
        elif value_field is not None:
            value = datatype.decode(getattr(metric, value_field))
        else:
            value = None
        return cls(
            timestamp=metric.timestamp if metric.HasField("timestamp") else None,
            name=metric.name if metric.HasField("name") else None,
            datatype=datatype,
            value=value,
            alias=metric.alias if metric.HasField("alias") else None,
            is_historical=metric.is_historical,
            is_transient=metric.is_transient,
            is_null=metric.is_null,
            metadata=Metadata.from_pb(metric.metadata)
            if metric.HasField("metadata")
            else None,
        )
