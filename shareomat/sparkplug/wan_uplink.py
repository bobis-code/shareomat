# -*- coding: utf-8 -*-
"""
File: shareomat/sparkplug/wan_uplink.py

WAN Uplink - Shareomat's Sparkplug Edge Node identity on the CENTRAL
(cross-house) broker, publishing only this site's own participants. See
docs/Architektur/Shareomat_CrossHouse_Sparkplug_Vertrag.md Abschnitt 2.1.

This is the same mechanism as the local ParticipantRelay (DBIRTH/DDATA/
DDEATH per participant) - not a new class, just a factory that points
ParticipantRelay at the central broker/LEG identity instead of the local
one, with NCMD disabled (Abschnitt 5: "NCMD/DCMD WAN-seitig zunaechst
vollstaendig verboten").

Origin/Loop Prevention (Abschnitt 3): whoever calls register_participant()/
update_participant()/deregister_participant() on the object this factory
returns MUST only ever pass this site's OWN local participant data (as
known by the local ParticipantRelay/Emsomat) - never data received from the
WAN Downlink. This module does not enforce that itself (it has no reference
to the Downlink or the central broker's incoming data at all) - the
separation is structural: build_wan_uplink() and WanDownlink are
independent objects with no shared state, so a loop can only happen if
calling code explicitly wires one into the other, which nothing here does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from shareomat.config import MqttConfig, SparkplugConfig, WanConfig

from .participant_relay import ParticipantRelay

if TYPE_CHECKING:
    import paho.mqtt.client as _mqtt_type


def build_wan_uplink(
    wan_config: WanConfig,
    *,
    client_id: str,
    client: Optional["_mqtt_type.Client"] = None,
) -> ParticipantRelay:
    """Builds a ParticipantRelay configured for the WAN Uplink role.

    Args:
        wan_config: Cross-House settings (broker, group_id=LEG-ID,
            edge_node_id=this site's WAN identity).
        client_id: MQTT client id to use for this connection (must differ
            from the local ParticipantRelay's client_id - separate session).
        client: injectable for tests, same convention as ParticipantRelay.
    """
    sp_config = SparkplugConfig(
        enabled=wan_config.enabled,
        group_id=wan_config.group_id,
        primary_host_id="",  # unused: WAN Uplink has no Primary Host to wait for (Abschnitt 2.1)
        emsomat_edge_node_id="",  # unused: this role never subscribes to an Emsomat
        participant_edge_node_id=wan_config.edge_node_id,
    )
    mqtt_config = MqttConfig(
        enabled=wan_config.enabled,
        broker=wan_config.broker,
        port=wan_config.port,
        username=wan_config.username,
        password=wan_config.password,
        client_id=client_id,
        tls_enabled=wan_config.tls_enabled,
        tls_ca_cert=wan_config.tls_ca_cert,
    )
    return ParticipantRelay(sp_config, mqtt_config, client=client, enable_ncmd=False)
