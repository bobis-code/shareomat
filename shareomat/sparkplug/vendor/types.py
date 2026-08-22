# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/types.py

Vendored from pysparkplug._types (Apache 2.0), see NOTICE.md.
"""

from __future__ import annotations

import datetime
import sys
from typing import Union

if sys.version_info < (3, 11):
    from typing_extensions import Self, TypeAlias
else:
    from typing import Self, TypeAlias

__all__ = ["MetricValue", "Self", "TypeAlias"]

MetricValue: TypeAlias = Union[
    int,
    float,
    bool,
    str,
    bytes,
    datetime.datetime,
    tuple[int, ...],
    tuple[float, ...],
    tuple[bool, ...],
    tuple[str, ...],
    tuple[datetime.datetime, ...],
]
"""Type annotation for the types a `Metric`'s `value` attribute can take"""
