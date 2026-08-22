#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
File: tools/manual_wan_broker_test.py

Manual, ad-hoc verification of the Cross-House Sparkplug B strecke
(see docs/Architektur/Shareomat_CrossHouse_Sparkplug_Vertrag.md) against a
REAL MQTT broker, using the real, production shareomat/sparkplug/* code
(not a fake/in-memory broker like the automated tests use).

Not a pytest test - not run in CI, no assertions library needed. Run it
yourself, locally, with your own credentials - this script reads them from
environment variables and never logs or stores them anywhere.

Usage (bash):
    export MQTT_HOST=homeassistant.local   # or the broker's IP
    export MQTT_PORT=1883
    export MQTT_USERNAME=your_username
    export MQTT_PASSWORD=your_password
    # optional: export MQTT_TLS=1   (if your broker requires TLS on that port)
    python tools/manual_wan_broker_test.py

Usage (PowerShell):
    $env:MQTT_HOST = "homeassistant.local"
    $env:MQTT_PORT = "1883"
    $env:MQTT_USERNAME = "your_username"
    $env:MQTT_PASSWORD = "your_password"
    python tools/manual_wan_broker_test.py

What it does:
    Simulates three independent Shareomat WAN identities against your real
    broker, using the SAME real classes production code uses
    (build_wan_uplink/WanDownlink/ParticipantRelay):
      - SHAREOMAT_A: WAN Uplink, publishes one test participant
      - SHAREOMAT_B: WAN Downlink, subscribed to the same test LEG group,
        feeds a (dummy, not really connected) local ParticipantRelay
      - SHAREOMAT_X: WAN Uplink on a DIFFERENT test LEG group, to verify
        cross-LEG isolation (B must never see X's participant)

    Only uses topics under spBv1.0/test-crosshouse-leg*/... - does not
    touch, read, or interfere with any real Home Assistant entity/topic on
    your broker.

Output is safe to paste back/share - it contains PASS/FAIL results only,
never your credentials (they are read from the environment and never
printed).
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shareomat.config import MqttConfig, SparkplugConfig, WanConfig  # noqa: E402
from shareomat.sparkplug.participant_relay import ParticipantRelay  # noqa: E402
from shareomat.sparkplug.wan_downlink import WanDownlink  # noqa: E402
from shareomat.sparkplug.wan_uplink import build_wan_uplink  # noqa: E402

MQTT_HOST = os.environ.get("MQTT_HOST", "homeassistant.local")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "")
MQTT_TLS = os.environ.get("MQTT_TLS", "").lower() in ("1", "true", "yes")

TEST_GROUP_ID = "test-crosshouse-leg"
FOREIGN_GROUP_ID = "test-crosshouse-leg-FOREIGN"

_failures: list[str] = []


def check(label: str, condition: bool) -> None:
    status = "OK" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        _failures.append(label)


def _wan_config(edge_node_id: str, own_participant_id: str, *, group_id: str = TEST_GROUP_ID) -> WanConfig:
    return WanConfig(
        enabled=True, broker=MQTT_HOST, port=MQTT_PORT,
        username=MQTT_USERNAME, password=MQTT_PASSWORD, tls_enabled=MQTT_TLS,
        group_id=group_id, edge_node_id=edge_node_id, own_participant_id=own_participant_id,
    )


def _dummy_local_relay(edge_node_id: str) -> ParticipantRelay:
    """A ParticipantRelay that never actually connects (client=object()) -
    stands in for "house B's real local Emsomat-facing relay" for this test;
    we only care what WanDownlink writes into its in-memory registry."""
    sp = SparkplugConfig(enabled=True, group_id="test-local", participant_edge_node_id=edge_node_id)
    mq = MqttConfig(enabled=True, broker="unused", port=1883, client_id=f"{edge_node_id}-dummy")
    return ParticipantRelay(sp, mq, client=object())


def main() -> None:
    if not MQTT_USERNAME:
        print("ERROR: set MQTT_USERNAME (and MQTT_PASSWORD) environment variables first. See module docstring.")
        sys.exit(2)

    print(f"Connecting to {MQTT_HOST}:{MQTT_PORT} (tls={MQTT_TLS}) ...")
    print(f"Test LEG group_id={TEST_GROUP_ID!r}, foreign group_id={FOREIGN_GROUP_ID!r}")
    print()

    # ---- SHAREOMAT_A: WAN Uplink, own participant TEST-P-A ----
    uplink_a = build_wan_uplink(_wan_config("TEST-SITE-A", "TEST-P-A"), client_id="test-site-a-uplink")
    check("SHAREOMAT_A WAN Uplink connects", uplink_a.start())

    # ---- SHAREOMAT_B: WAN Downlink, feeds a local relay ----
    relay_b = _dummy_local_relay("TEST-SITE-B-relay")
    downlink_b = WanDownlink(_wan_config("TEST-SITE-B", "TEST-P-B"), relay_b, client_id="test-site-b-downlink")
    check("SHAREOMAT_B WAN Downlink connects", downlink_b.start())

    time.sleep(1.0)  # let both sessions settle

    # ---- normal data flow: A -> real broker -> B ----
    ORIGINAL_TS = 1700000000000
    uplink_a.register_participant("TEST-P-A", export_now_w=1234.0, import_now_w=56.0, timestamp_ms=ORIGINAL_TS)
    time.sleep(1.5)  # real network round-trip

    check("B's local relay received A's participant", "TEST-P-A" in relay_b.known_participants)
    check("ExportPower value correct (1234.0 W)", relay_b._participants.get("TEST-P-A") == (1234.0, 56.0))

    remote_birth = downlink_b._remote_births.get(("TEST-SITE-A", "TEST-P-A"))
    ts = None
    if remote_birth:
        ts = next((m.timestamp for m in remote_birth.metrics if m.name == "Market/ExportPowerNow"), None)
    check("Original metric timestamp preserved over the real broker", ts == ORIGINAL_TS)

    # ---- NDEATH cleanup: A disconnects, B must remove its participant ----
    uplink_a.stop()
    time.sleep(1.5)
    check("B's local relay cleared after A's NDEATH", relay_b.known_participants == ())

    # ---- reconnect: SAME uplink_a object reconnects (a fresh object would
    # legitimately start with an empty participant registry - that is not
    # what "reconnect" means here; a real network blip/reconnect keeps the
    # same long-lived object and its _participants dict intact, see
    # ParticipantRelay.start()'s rebirth loop) ----
    check("SHAREOMAT_A reconnects (same object, same known participants)", uplink_a.start())
    time.sleep(1.5)
    check("B's local relay sees A's participant again after reconnect", "TEST-P-A" in relay_b.known_participants)

    # ---- isolation: a client on a DIFFERENT LEG group must never be seen by B ----
    uplink_x = build_wan_uplink(_wan_config("TEST-SITE-X", "TEST-P-X", group_id=FOREIGN_GROUP_ID), client_id="test-site-x-uplink-foreign")
    check("SHAREOMAT_X (foreign LEG) connects", uplink_x.start())
    uplink_x.register_participant("TEST-P-X", export_now_w=9999.0, import_now_w=0.0, timestamp_ms=1700000001000)
    time.sleep(1.5)

    check("Foreign-LEG participant never reaches B's relay", "TEST-P-X" not in relay_b.known_participants)
    check("B's relay still only knows the real test LEG's participant", set(relay_b.known_participants) == {"TEST-P-A"})

    # ---- cleanup ----
    uplink_a.stop()
    uplink_x.stop()
    downlink_b.stop()
    time.sleep(0.5)

    print()
    if _failures:
        print(f"{len(_failures)} CHECK(S) FAILED:")
        for f in _failures:
            print(f"  - {f}")
        sys.exit(1)
    print("ALL CHECKS PASSED (real broker, real network, real Sparkplug B wire protocol)")


if __name__ == "__main__":
    main()
