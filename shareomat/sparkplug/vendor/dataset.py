# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/dataset.py

NOT vendored from pysparkplug - pysparkplug 0.6.1 explicitly marks
DataType.DATASET as "Unsupported" (message-typed metric values need
CopyFrom() semantics, not the plain setattr()-based scalar path used by
metric.py for INT/DOUBLE/STRING/etc.). Implemented here directly against the
vendored `sparkplug_b_pb2` schema because Emsomat's `LEG/DemandForecast`
metric (a 15-min-slot forecast series, see Emsomat/shareomat/models.py
LegDemandForecast) is a genuine table, not a scalar - see
docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md (Shareomat repo) for the
mapping this supports.

Only the scalar DataSet column types Emsomat actually needs are supported
(INT64/DOUBLE/STRING/BOOLEAN) - extend _COLUMN_ENCODERS/_COLUMN_DECODERS if a
future column needs a different type. Nested DataSet/Template values inside a
DataSet cell are out of scope (not needed, not attempted).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Sequence, Union

from .datatype import DataType
from .protobuf_types import DataSet as PB_DataSet
from .types import Self

__all__ = ["Column", "Dataset"]

_ColumnValue = Union[int, float, str, bool]

_COLUMN_VALUE_FIELDS = {
    DataType.INT64: "long_value",
    DataType.UINT64: "long_value",
    DataType.DOUBLE: "double_value",
    DataType.FLOAT: "float_value",
    DataType.STRING: "string_value",
    DataType.TEXT: "string_value",
    DataType.BOOLEAN: "boolean_value",
    DataType.INT32: "int_value",
    DataType.UINT32: "int_value",
}


@dataclasses.dataclass(frozen=True)
class Column:
    """One DataSet column: name + Sparkplug scalar datatype."""

    name: str
    datatype: DataType

    def __post_init__(self) -> None:
        if self.datatype not in _COLUMN_VALUE_FIELDS:
            raise NotImplementedError(
                f"DataSet column datatype {self.datatype.name} not supported"
            )


@dataclasses.dataclass(frozen=True)
class Dataset:
    """A Sparkplug B DataSet metric value: columns + rows.

    Args:
        columns:
            ordered column definitions
        rows:
            each row is a tuple with one value per column, in column order.
            A `None` entry means that cell is null for that row.
    """

    columns: tuple[Column, ...]
    rows: tuple[tuple[Any, ...], ...]

    def __post_init__(self) -> None:
        for row in self.rows:
            if len(row) != len(self.columns):
                raise ValueError(
                    f"row {row!r} has {len(row)} values, expected {len(self.columns)}"
                )

    def to_pb(self) -> PB_DataSet:
        ds = PB_DataSet()
        ds.num_of_columns = len(self.columns)
        ds.columns.extend(col.name for col in self.columns)
        ds.types.extend(int(col.datatype) for col in self.columns)
        for row in self.rows:
            pb_row = ds.rows.add()
            for col, value in zip(self.columns, row):
                pb_value = pb_row.elements.add()
                field = _COLUMN_VALUE_FIELDS[col.datatype]
                if value is None:
                    # DataSetValue has no is_null flag of its own in the
                    # proto (unlike Metric) - callers must not put null cells
                    # in a DataSet; validate early instead of silently
                    # encoding a wrong default.
                    raise ValueError(
                        f"DataSet column {col.name!r} does not support null cells"
                    )
                setattr(pb_value, field, value)
        return ds

    @classmethod
    def from_pb(cls, ds: PB_DataSet) -> Self:
        columns = tuple(
            Column(name=name, datatype=DataType(dtype))
            for name, dtype in zip(ds.columns, ds.types)
        )
        rows = tuple(
            tuple(
                getattr(pb_value, _COLUMN_VALUE_FIELDS[col.datatype])
                for col, pb_value in zip(columns, pb_row.elements)
            )
            for pb_row in ds.rows
        )
        return cls(columns=columns, rows=rows)
