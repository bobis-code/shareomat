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
import sys
from pathlib import Path

import yaml

OPTIONS_PATH = Path("/data/options.json")
CONFIG_OUTPUT = Path("/config/shareomat/leg_config.yaml")
ENV_OUTPUT = Path("/tmp/shareomat_env")

# Runtime (technical) keys this script owns; every other top-level key in an
# existing CONFIG_OUTPUT file (legacy "leg"/"participants"/"meters"/"tariffs")
# is preserved as-is until Shareomat's one-time database import consumes it.
_RUNTIME_SECTIONS = {"paths", "mqtt", "email", "web"}


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
        },
    }

    config = {**legacy_sections, **runtime_config}

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
