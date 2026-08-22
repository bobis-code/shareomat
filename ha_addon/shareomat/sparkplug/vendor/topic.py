# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/topic.py

Vendored from pysparkplug._topic (Apache 2.0), see NOTICE.md.
"""

import dataclasses
import re
from typing import Optional, Union, cast

from .constants import (
    MULTI_LEVEL_WILDCARD,
    MULTI_LEVEL_WILDCARD_TYPE,
    SINGLE_LEVEL_WILDCARD,
    SINGLE_LEVEL_WILDCARD_TYPE,
)
from .enums import MessageType
from .types import Self, TypeAlias

__all__ = ["Topic"]
WILDCARDS = {SINGLE_LEVEL_WILDCARD, MULTI_LEVEL_WILDCARD}
Wildcard: TypeAlias = Union[SINGLE_LEVEL_WILDCARD_TYPE, MULTI_LEVEL_WILDCARD_TYPE]


@dataclasses.dataclass(frozen=True)
class Topic:
    """Class representing a Sparkplug B topic.

    group_id/message_type/edge_node_id/device_id form the device/edge-node
    namespace (spBv1.0/<group_id>/<message_type>/<edge_node_id>[/<device_id>]);
    sparkplug_host_id forms the separate Primary Host STATE namespace
    (spBv1.0/STATE/<sparkplug_host_id>).
    """

    namespace = "spBv1.0"
    group_id: Optional[str] = None
    message_type: Optional[Union[MessageType, Wildcard]] = None
    edge_node_id: Optional[str] = None
    device_id: Optional[str] = None
    sparkplug_host_id: Optional[str] = None

    _validator = re.compile(f"[/{SINGLE_LEVEL_WILDCARD}{MULTI_LEVEL_WILDCARD}]")

    def __post_init__(self) -> None:
        for field_name in ("message_type", "group_id", "edge_node_id", "device_id", "sparkplug_host_id"):
            value = getattr(self, field_name)
            if value is not None and value not in WILDCARDS and self._validator.search(value) is not None:
                raise ValueError(
                    f"{field_name} {value} cannot contain /, "
                    f"{SINGLE_LEVEL_WILDCARD}, or {MULTI_LEVEL_WILDCARD} characters"
                )

    @classmethod
    def from_str(cls, topic: str) -> Self:
        """Construct a Topic object from a topic string"""
        parts = topic.split("/")
        namespace = parts[0]
        if namespace != cls.namespace:
            raise ValueError(f"Topic with invalid namespace {namespace}")
        if parts[1] == MessageType.STATE:
            return cls(message_type=MessageType.STATE, sparkplug_host_id=parts[2])

        group_id = None
        message_type = None
        edge_node_id = None
        device_id = None

        try:
            group_id = parts[1]
            message_type = cast(
                Union[MessageType, Wildcard],
                parts[2] if parts[2] in WILDCARDS else MessageType(parts[2]),
            )
            edge_node_id = parts[3]
            device_id = parts[4]
        except IndexError:
            pass
        return cls(
            group_id=group_id,
            message_type=message_type,
            edge_node_id=edge_node_id,
            device_id=device_id,
        )

    def __str__(self) -> str:
        """Encode a Topic object as a string"""
        if self.message_type == MessageType.STATE:
            return f"{self.namespace}/{self.message_type}/{self.sparkplug_host_id}"
        if self.device_id is not None:
            return f"{self.namespace}/{self.group_id}/{self.message_type}/{self.edge_node_id}/{self.device_id}"
        if self.edge_node_id is not None:
            return f"{self.namespace}/{self.group_id}/{self.message_type}/{self.edge_node_id}"
        if self.message_type is not None:
            return f"{self.namespace}/{self.group_id}/{self.message_type}"
        return f"{self.namespace}/{self.group_id}"
