# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/payload.py

Vendored from pysparkplug._payload (Apache 2.0), see NOTICE.md.
"""

from __future__ import annotations

import dataclasses
from abc import abstractmethod
from typing import Optional, Protocol, cast, runtime_checkable

from . import protobuf_types as protobuf
from .datatype import DataType
from .metric import Metric
from .types import Self

__all__ = [
    "DBirth",
    "DCmd",
    "DData",
    "DDeath",
    "NBirth",
    "NCmd",
    "NData",
    "NDeath",
    "State",
]


@runtime_checkable
class Payload(Protocol):
    """Protocol defining the methods a payload should have"""

    @classmethod
    @abstractmethod
    def decode(cls, raw: bytes, *, birth: Optional[Birth] = None) -> Self:
        raise NotImplementedError()

    @abstractmethod
    def encode(self, *, include_dtypes: bool = False) -> bytes:
        raise NotImplementedError()


class _PBPayload:
    @classmethod
    def decode(cls, raw: bytes, *, birth: Optional[Birth] = None) -> Self:
        payload = protobuf.Payload.FromString(raw)
        if birth is not None:
            for metric in payload.metrics:
                if not metric.name:
                    metric.name = birth.get_name(metric.alias)
                if metric.datatype == DataType.UNKNOWN:
                    metric.datatype = birth.get_dtype(metric.name)
        if not payload.HasField("timestamp"):
            raise ValueError("Sparkplug payload missing required timestamp field")
        kwargs = {
            "timestamp": payload.timestamp,
            "metrics": tuple(Metric.from_pb(metric) for metric in payload.metrics),
        }
        if payload.HasField("seq"):
            kwargs["seq"] = payload.seq
        return cls(**kwargs)

    def encode(self, *, include_dtypes: bool = False) -> bytes:
        payload = protobuf.Payload()
        payload.timestamp = self.timestamp  # type: ignore[attr-defined]
        if hasattr(self, "seq"):
            payload.seq = self.seq  # type: ignore[reportAttributeAccessIssue]
        payload.metrics.extend(
            metric.to_pb(include_dtype=include_dtypes)
            for metric in self.metrics  # type: ignore[attr-defined]
        )
        return cast(bytes, payload.SerializeToString())


@dataclasses.dataclass(frozen=True)
class Birth(_PBPayload):
    """Class representing a Birth payload (NBIRTH/DBIRTH base)"""

    timestamp: int
    seq: int
    metrics: tuple[Metric, ...]
    _names_mapping: dict[int, str] = dataclasses.field(
        init=False, default_factory=dict, repr=False
    )
    _dtypes_mapping: dict[str, DataType] = dataclasses.field(
        init=False, default_factory=dict, repr=False
    )

    def __post_init__(self) -> None:
        """Validates payload"""
        for metric in self.metrics:
            if metric.name is None:
                raise ValueError(
                    f"Metric {metric} must have a defined name when provided to a Birth payload"
                )
            if metric.datatype == DataType.UNKNOWN:
                raise ValueError(
                    f"Metric {metric} must have a defined datatype when provided to a Birth payload"
                )
            if metric.alias is not None:
                self._names_mapping[metric.alias] = metric.name
            self._dtypes_mapping[metric.name] = metric.datatype

    @classmethod
    def decode(cls, raw: bytes, *, birth: Optional[Birth] = None) -> Self:
        birth = None  # don't use previous birth to determine name/datatypes
        return super().decode(raw, birth=birth)

    def encode(self, *, include_dtypes: bool = False) -> bytes:
        include_dtypes = True  # always include datatypes
        return super().encode(include_dtypes=include_dtypes)

    def get_name(self, alias: int) -> str:
        return self._names_mapping[alias]

    def get_dtype(self, name: str) -> DataType:
        return self._dtypes_mapping[name]


class NBirth(Birth):
    """Class representing an NBIRTH payload"""


class DBirth(Birth):
    """Class representing a DBIRTH payload"""


@dataclasses.dataclass(frozen=True)
class _Data(_PBPayload):
    timestamp: int
    seq: int
    metrics: tuple[Metric, ...]


class NData(_Data):
    """Class representing an NDATA payload"""


class DData(_Data):
    """Class representing a DDATA payload"""


@dataclasses.dataclass(frozen=True)
class _Cmd(_PBPayload):
    timestamp: int
    metrics: tuple[Metric, ...]


class NCmd(_Cmd):
    """Class representing an NCMD payload"""


class DCmd(_Cmd):
    """Class representing a DCMD payload"""


@dataclasses.dataclass(frozen=True)
class NDeath:
    """Class representing an NDEATH payload (single bdSeq metric)"""

    timestamp: Optional[int]
    bd_seq_metric: Metric

    @classmethod
    def decode(
        cls,
        raw: bytes,
        *,
        birth: Optional[Birth] = None,
    ) -> Self:
        payload = protobuf.Payload.FromString(raw)
        return cls(
            timestamp=payload.timestamp if not payload.HasField("timestamp") else None,
            bd_seq_metric=Metric.from_pb(payload.metrics[0]),
        )

    def encode(self, *, include_dtypes: bool = False) -> bytes:
        include_dtypes = True  # always include datatypes
        payload = protobuf.Payload()
        if self.timestamp is not None:
            payload.timestamp = self.timestamp
        payload.metrics.append(self.bd_seq_metric.to_pb(include_dtype=include_dtypes))
        return cast(bytes, payload.SerializeToString())


@dataclasses.dataclass(frozen=True)
class DDeath:
    """Class representing a DDEATH payload"""

    timestamp: int
    seq: int

    @classmethod
    def decode(
        cls,
        raw: bytes,
        *,
        birth: Optional[Birth] = None,
    ) -> Self:
        payload = protobuf.Payload.FromString(raw)
        return cls(
            timestamp=payload.timestamp,
            seq=payload.seq,
        )

    def encode(self, *, include_dtypes: bool = False) -> bytes:
        payload = protobuf.Payload()
        payload.timestamp = self.timestamp
        payload.seq = self.seq
        return cast(bytes, payload.SerializeToString())


@dataclasses.dataclass(frozen=True)
class State:
    """Class representing a STATE payload (Primary Host Application online/offline).

    NOT vendored from pysparkplug's own State class - pysparkplug 0.6.1 encodes
    STATE as JSON (`{"timestamp": ..., "online": ...}`), which was the pre-3.0
    convention. Sparkplug 3.0's normative text is explicit that the STATE payload
    MUST be the bare UTF-8 string "ONLINE" or "OFFLINE" (see
    docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md Abschnitt 28.2 in the
    Shareomat repo, quoting eclipse-sparkplug/sparkplug/docs/normative_statements.md).
    `timestamp` is kept as a local-only Python field (e.g. "when did we see this
    STATE message") and is intentionally NOT part of the wire payload.
    """

    online: bool
    timestamp: Optional[int] = None

    @classmethod
    def decode(
        cls,
        raw: bytes,
        *,
        birth: Optional[Birth] = None,
    ) -> Self:
        text = raw.decode("utf-8").strip()
        if text == "ONLINE":
            return cls(online=True)
        if text == "OFFLINE":
            return cls(online=False)
        raise ValueError(f"STATE payload must be 'ONLINE' or 'OFFLINE', got {text!r}")

    def encode(self, *, include_dtypes: bool = False) -> bytes:
        return b"ONLINE" if self.online else b"OFFLINE"
