# -*- coding: utf-8 -*-
"""
File: generate_config.py

Purpose:
    Reads Home Assistant add-on options from /data/options.json and
    generates /config/shareomat/leg_config.yaml for Shareomat.
    Also writes /tmp/shareomat_env so run.sh can export runtime
    environment variables (timezone, log level, dry-run flag).

Part of:
    Shareomat — Home Assistant Add-on

Notes:
    Called once by run.sh at container startup, before the engine starts.
    Regenerates the config on every restart — options changes take effect
    after an add-on restart.

    Each metering_point in options.json maps to exactly one participant and
    one meter in leg_config.yaml (1:1).  The mpid is used as both
    participant_id and meter_id.  role drives the participant_type mapping
    and the meter role; it is used only for validate_config startup checks —
    matching and billing remain fully data-driven (import/export direction).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

OPTIONS_PATH = Path("/data/options.json")
CONFIG_OUTPUT = Path("/config/shareomat/leg_config.yaml")
ENV_OUTPUT = Path("/tmp/shareomat_env")

# role → participant_type translation.
# grid meters are modelled as producer_consumer so validate_config accepts them.
_ROLE_TO_PARTICIPANT_TYPE: dict[str, str] = {
    "producer":          "producer",
    "consumer":          "consumer",
    "producer_consumer": "producer_consumer",
    "grid":              "producer_consumer",
}


def _warn_metering_points(points: list[dict]) -> None:
    """Print early hints for common meter setup mistakes.

    The generated leg_config.yaml is still written even when the meter setup is
    incomplete. main.py performs the authoritative validation and can then show
    the exact startup error in the Ingress dashboard instead of the add-on
    exiting before the user sees a useful screen.
    """
    if not points:
        print(
            "[WARNING] No metering_points configured!\n"
            "        Go to Add-on → Configuration → metering_points and add\n"
            "        at least one meter with the real MPID from your energy provider.",
        )
        return

    roles = [mp.get("role", "") for mp in points]
    has_producer = any(r in ("producer", "producer_consumer") for r in roles)
    has_consumer = any(r in ("consumer", "producer_consumer") for r in roles)

    if not has_producer:
        print(
            "[WARNING] No producer meter configured.\n"
            "        At least one meter must have role 'producer' or 'producer_consumer'.\n"
            "        Use 'producer_consumer' if your meter both produces (PV) and consumes.",
        )

    if not has_consumer:
        print(
            "[WARNING] No consumer meter configured.\n"
            "          At least one meter must have role 'consumer' or 'producer_consumer'.\n"
            "          Use 'producer_consumer' if your meter both produces (PV) and consumes."
        )


def main() -> None:
    if not OPTIONS_PATH.exists():
        print(f"[ERROR] Options file not found: {OPTIONS_PATH}", file=sys.stderr)
        sys.exit(1)

    with OPTIONS_PATH.open(encoding="utf-8-sig") as f:
        opts: dict = json.load(f)

    _warn_metering_points(opts.get("metering_points", []))
    dry_run: bool = opts.get("dry_run", False)

    config = {
        "leg": {
            "community_id": opts["community_id"],
            "name": opts["community_name"],
        },
        "participants": [
            {
                "participant_id": mp["mpid"],
                "label": mp["label"],
                "participant_type": _ROLE_TO_PARTICIPANT_TYPE.get(
                    mp["role"], "producer_consumer"
                ),
                "active": True,
            }
            for mp in opts.get("metering_points", [])
        ],
        "meters": [
            {
                "meter_id": mp["mpid"],
                "participant_id": mp["mpid"],
                "label": mp["label"],
                "role": mp["role"],
                "active": True,
            }
            for mp in opts.get("metering_points", [])
        ],
        "tariffs": {
            "local_rate_chf_kwh": float(opts["local_rate_chf_kwh"]),
            "grid_rate_chf_kwh": float(opts["grid_rate_chf_kwh"]),
            "feed_in_rate_chf_kwh": float(opts["feed_in_rate_chf_kwh"]),
        },
        "paths": {
            "inbox":        "/config/shareomat/inbox",
            "archive":      "/config/shareomat/archive",
            "reports":      "/config/shareomat/reports",
            "state":        "/config/shareomat/state",
            "share_inbox":  str(opts.get("share_inbox", "")),
        },
        "processing": {
            "slot_minutes": 15,
            "archive_processed": not dry_run,
            "unknown_meter_policy": opts.get("unknown_meter_policy", "skip"),
            "cron_schedule": str(opts.get("cron_schedule", "")),
            "auto_scan_enabled": bool(opts.get("auto_scan_enabled", False)),
            "scan_interval_seconds": int(opts.get("scan_interval_seconds", 60)),
        },
        "mqtt": {
            "enabled":                True,
            "broker":                 opts["mqtt_host"],
            "port":                   int(opts.get("mqtt_port", 1883)),
            "username":               opts.get("mqtt_username", ""),
            "password":               opts.get("mqtt_password", ""),
            "client_id":              "shareomat",
            "topic_prefix":           opts.get("base_topic", "shareomat"),
            "discovery_prefix":       opts.get("discovery_prefix", "homeassistant"),
            "discovery_enabled":      True,
            "command_topic_enabled":  bool(opts.get("command_topic_enabled", True)),
            "qos":                    1,
            "retain":                 True,
            "tls_enabled":            bool(opts.get("mqtt_tls", False)),
            "tls_ca_cert":            str(opts.get("mqtt_ca_cert", "")),
        },
        "email": {
            "enabled":              bool(opts.get("email_enabled", False)),
            "imap_host":            opts.get("email_imap_host", "imap.gmail.com"),
            "imap_port":            int(opts.get("email_imap_port", 993)),
            "username":             opts.get("email_username", ""),
            "password":             opts.get("email_password", ""),
            "folder":               opts.get("email_folder", "INBOX"),
            "allowed_senders":      list(opts.get("email_allowed_senders", [])),
            "poll_interval_seconds": int(opts.get("email_poll_interval_seconds", 300)),
        },
        "ingress": {
            "enabled": bool(opts.get("ingress_enabled", True)),
            "port": 8099,
        },
    }

    CONFIG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_OUTPUT.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"[INFO] Config written to {CONFIG_OUTPUT}")

    # Write env vars for run.sh to source
    timezone = opts.get("timezone", "Europe/Zurich")
    log_level = opts.get("log_level", "info").upper()
    env_lines = [
        f'export SHAREOMAT_CONFIG_PATH="{CONFIG_OUTPUT}"',
        f'export SHAREOMAT_TZ="{timezone}"',
        f'export SHAREOMAT_LOG_LEVEL="{log_level}"',
        f'export SHAREOMAT_DRY_RUN="{str(dry_run).lower()}"',
    ]
    ENV_OUTPUT.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    print(f"[INFO] Env file written to {ENV_OUTPUT}")


if __name__ == "__main__":
    main()
