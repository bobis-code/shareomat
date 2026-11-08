# -*- coding: utf-8 -*-
"""
File: generate_runtime_config.py

Purpose:
    Reads Home Assistant add-on options from /data/options.json and
    generates the technical runtime configuration Shareomat reads at
    startup (paths, MQTT, e-mail import, web port). Also writes
    /tmp/shareomat_env so run.sh can export runtime environment
    variables (timezone, log level).

Part of:
    Shareomat — Home Assistant Add-on

Notes:
    Renamed from generate_config.py. Gemeinschaft, Teilnehmer, Messpunkte,
    Tarife, and Automatik are NOT generated here anymore — they live in
    the SQLite admin database (/data/shareomat.db), managed through the
    Shareomat web interface itself. See SHAREOMAT_UMBAU_STRUKTUR.md.

    Called once by run.sh at container startup, before the engine starts.
    Regenerates the technical sections of the config on every restart;
    the community/participants/meters/tariffs sections of a PRE-EXISTING
    file are left untouched (not regenerated, not deleted) so that an
    installation upgrading from an older Shareomat version still has that
    legacy data available for the one-time SQLite import Shareomat
    performs on its own next startup. Once that import has happened, the
    legacy sections are never read again and become inert.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import yaml

OPTIONS_PATH = Path("/data/options.json")
CONFIG_OUTPUT = Path("/config/shareomat/leg_config.yaml")
ENV_OUTPUT = Path("/tmp/shareomat_env")
EMSOMAT_MANIFEST = Path("/config/custom_components/emsomat/manifest.json")

# Emsomat's own defaults: what mode "auto" connects to.
_SPARKPLUG_DEFAULT_NODE = "emsomat-node"
_SPARKPLUG_DEFAULT_GROUP = "emsomat"
# Both values become MQTT topic levels: no wildcards, separators or spaces.
_SPARKPLUG_ID = re.compile(r"^[^/+#\s\x00-\x1f\x7f]{1,64}$")

# Runtime (technical) keys this script owns; every other top-level key in an
# existing CONFIG_OUTPUT file (legacy "leg"/"participants"/"meters"/"tariffs")
# is preserved as-is until Shareomat's one-time database import consumes it.
_RUNTIME_SECTIONS = {"paths", "mqtt", "email", "web"}


def _sparkplug_section(opts: dict, existing: dict) -> dict | None:
    """The local Emsomat channel, or None to leave a hand-written block in place.

    Add-on option section `sparkplug`, field `mode`:
      auto    Emsomat on this Home Assistant is connected with its default IDs.
      manual  the entered node_id and group_id are used.
      off     no connection.

    Any problem switches only Sparkplug off (fail closed), never the add-on:
    billing and the admin page keep working.
    """
    raw = opts.get("sparkplug")
    chosen = raw if isinstance(raw, dict) else {}
    mode = str(chosen.get("mode", "auto"))
    block = existing.get("sparkplug")
    if mode == "auto" and isinstance(block, dict) and not block.get("managed_by_addon"):
        print(f"[INFO] Keeping the sparkplug section written by hand in {CONFIG_OUTPUT}.")
        return None

    group_id, node_id = _SPARKPLUG_DEFAULT_GROUP, _SPARKPLUG_DEFAULT_NODE
    if mode == "manual":
        group_id = str(chosen.get("group_id", "")).strip()
        node_id = str(chosen.get("node_id", "")).strip()
        enabled = bool(_SPARKPLUG_ID.match(group_id) and _SPARKPLUG_ID.match(node_id))
        if not enabled:
            print("[ERROR] sparkplug node_id or group_id is not valid - Sparkplug stays off.", file=sys.stderr)
            group_id, node_id = _SPARKPLUG_DEFAULT_GROUP, ""
    else:
        enabled = mode == "auto" and EMSOMAT_MANIFEST.is_file()
        if mode == "auto" and not enabled:
            print("[INFO] Emsomat is not installed on this Home Assistant - Sparkplug stays off.")
    if enabled:
        print(f"[INFO] Sparkplug on ({mode}): group_id={group_id} node_id={node_id}")
    return {
        "enabled": enabled,
        "group_id": group_id,
        "primary_host_id": "shareomat",
        "emsomat_edge_node_id": node_id,
        "participant_edge_node_id": "shareomat-relay",
        "managed_by_addon": True,
    }


def main() -> None:
    if not OPTIONS_PATH.exists():
        print(f"[ERROR] Options file not found: {OPTIONS_PATH}", file=sys.stderr)
        sys.exit(1)

    with OPTIONS_PATH.open(encoding="utf-8-sig") as f:
        opts: dict = json.load(f)

    existing: dict = {}
    if CONFIG_OUTPUT.exists():
        try:
            with CONFIG_OUTPUT.open(encoding="utf-8") as f:
                existing = yaml.safe_load(f) or {}
        except Exception as exc:
            print(f"[WARNING] Could not read existing {CONFIG_OUTPUT}, starting fresh: {exc}")

    legacy_sections = {k: v for k, v in existing.items() if k not in _RUNTIME_SECTIONS}
    if legacy_sections:
        print(
            f"[INFO] Preserving legacy section(s) {sorted(legacy_sections)} from {CONFIG_OUTPUT} "
            "for Shareomat's one-time database import.",
        )

    runtime_config = {
        "paths": {
            "inbox":        "/config/shareomat/inbox",
            "archive":      "/config/shareomat/archive",
            "reports":      "/config/shareomat/reports",
            "state":        "/config/shareomat/state",
            "share_inbox":  str(opts.get("share_inbox", "")),
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
        "web": {
            "enabled": bool(opts.get("ingress_enabled", True)),
            "port": 8099,
            # Forgotten password: a fresh one-time code in the log allows a new one.
            "reset_login": bool(opts.get("reset_login", False)),
        },
    }

    config = {**legacy_sections, **runtime_config}
    sparkplug = _sparkplug_section(opts, existing)
    if sparkplug is not None:
        config["sparkplug"] = sparkplug

    CONFIG_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with CONFIG_OUTPUT.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    print(f"[INFO] Runtime config written to {CONFIG_OUTPUT}")

    timezone = opts.get("timezone", "Europe/Zurich")
    log_level = opts.get("log_level", "info").upper()
    env_lines = [
        f'export SHAREOMAT_CONFIG_PATH="{CONFIG_OUTPUT}"',
        f'export SHAREOMAT_DB_PATH="/data/shareomat.db"',
        f'export SHAREOMAT_TZ="{timezone}"',
        f'export SHAREOMAT_LOG_LEVEL="{log_level}"',
    ]
    ENV_OUTPUT.write_text("\n".join(env_lines) + "\n", encoding="utf-8")
    print(f"[INFO] Env file written to {ENV_OUTPUT}")


if __name__ == "__main__":
    main()
