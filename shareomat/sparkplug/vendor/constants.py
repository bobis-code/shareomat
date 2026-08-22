# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/constants.py

Vendored from pysparkplug._constants (Apache 2.0), see NOTICE.md. Client
connection defaults (port/keepalive/bind_address/blocking) dropped - Emsomat's
own MqttAdapter owns connection config, see sparkplug/edge_node.py.
"""

from typing import Literal

from .types import TypeAlias

SINGLE_LEVEL_WILDCARD_TYPE: TypeAlias = Literal["+"]
SINGLE_LEVEL_WILDCARD: SINGLE_LEVEL_WILDCARD_TYPE = "+"

MULTI_LEVEL_WILDCARD_TYPE: TypeAlias = Literal["#"]
MULTI_LEVEL_WILDCARD: MULTI_LEVEL_WILDCARD_TYPE = "#"
