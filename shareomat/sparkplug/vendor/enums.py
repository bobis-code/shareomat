# -*- coding: utf-8 -*-
"""
File: emsomat/sparkplug/vendor/enums.py

Adapted from pysparkplug._enums (Apache 2.0), see NOTICE.md. Trimmed to
MessageType/QoS only - the original also defined ErrorCode/ConnackCode/
MQTTProtocol/Transport, which are specific to pysparkplug's own MQTT client
and unused here (Emsomat's MqttAdapter has its own equivalents where needed).
"""

import enum

from . import payload
from .strenum import StrEnum

__all__ = ["MessageType", "QoS"]


class QoS(enum.IntEnum):
    """MQTT quality of service enum"""

    AT_MOST_ONCE = 0
    AT_LEAST_ONCE = 1
    EXACTLY_ONCE = 2


class MessageType(StrEnum):
    """Sparkplug B message type enum"""

    STATE = "STATE"
    NBIRTH = "NBIRTH"
    NDATA = "NDATA"
    NCMD = "NCMD"
    NDEATH = "NDEATH"
    DBIRTH = "DBIRTH"
    DDATA = "DDATA"
    DCMD = "DCMD"
    DDEATH = "DDEATH"

    @property
    def payload_cls(self) -> type:
        """Returns the payload class for this message type"""
        return _payloads[self]


_payloads = {
    MessageType.STATE: payload.State,
    MessageType.NBIRTH: payload.NBirth,
    MessageType.DBIRTH: payload.DBirth,
    MessageType.NDATA: payload.NData,
    MessageType.DDATA: payload.DData,
    MessageType.NCMD: payload.NCmd,
    MessageType.DCMD: payload.DCmd,
    MessageType.NDEATH: payload.NDeath,
    MessageType.DDEATH: payload.DDeath,
}
