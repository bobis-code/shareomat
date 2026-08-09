# -*- coding: utf-8 -*-
"""
File: shareomat/config.py

Purpose:
    Technical runtime configuration (paths, MQTT, e-mail import, web
    server port) and the fully assembled LegConfig the settlement core
    consumes. Replaces the old shareomat/core/leg_config.py.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    LEG/ZEV master data (community, participants, meters, tariff,
    operating settings) is NOT read here — it lives in SQLite and is
    resolved by shareomat.database.config_builder.build_leg_config(),
    which combines a RuntimeConfig (this module) with the database
    contents into one LegConfig. See SHAREOMAT_UMBAU_STRUKTUR.md §3-4.

    validate_leg_config() raises ValueError on the first detected group
    of errors so the engine never runs with a broken configuration.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from shareomat.leg_const import (
    DEFAULT_IMAP_PORT,
    DEFAULT_MQTT_PORT,
    DEFAULT_TOPIC_PREFIX,
    METER_ROLE_CONSUMER,
    METER_ROLE_GRID,
    METER_ROLE_PRODUCER,
    METER_ROLE_PRODUCER_CONSUMER,
    PARTICIPANT_TYPE_CONSUMER,
    PARTICIPANT_TYPE_PRODUCER,
    PARTICIPANT_TYPE_PRODUCER_CONSUMER,
    PARTICIPANT_TYPE_STORAGE,
    SLOT_MINUTES,
    UNKNOWN_METER_POLICY_FAIL,
    UNKNOWN_METER_POLICY_SKIP,
)
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff

logger = logging.getLogger(__name__)

_VALID_METER_ROLES = {
    METER_ROLE_PRODUCER,
    METER_ROLE_CONSUMER,
    METER_ROLE_PRODUCER_CONSUMER,
    METER_ROLE_GRID,
}

_VALID_PARTICIPANT_TYPES = {
    PARTICIPANT_TYPE_PRODUCER,
    PARTICIPANT_TYPE_CONSUMER,
    PARTICIPANT_TYPE_PRODUCER_CONSUMER,
    PARTICIPANT_TYPE_STORAGE,
}

_VALID_METER_POLICIES = {UNKNOWN_METER_POLICY_FAIL, UNKNOWN_METER_POLICY_SKIP}


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class PathConfig:
    """Runtime filesystem paths for the settlement engine."""

    inbox: Path
    archive: Path
    reports: Path
    state: Path
    share_inbox: str = ""  # External share folder to watch; empty = disabled


@dataclass
class ProcessingConfig:
    """Resolved processing rules for one settlement run.

    slot_minutes is a fixed Swiss LEG/ZEV constant; every other field is
    populated from the database-backed OperationSettings by
    shareomat.database.config_builder.build_leg_config().
    """

    slot_minutes: int = SLOT_MINUTES
    archive_processed: bool = True
    unknown_meter_policy: str = UNKNOWN_METER_POLICY_FAIL
    cron_schedule: str = ""
    auto_scan_enabled: bool = False
    scan_interval_seconds: int = 60
    peak_start_hour: int = 6               # Hochtarif-Fenster für Tariff.rate_mode="ht_nt"
    peak_end_hour: int = 22
    peak_weekdays_only: bool = True


@dataclass
class MqttConfig:
    """MQTT broker and topic configuration."""

    enabled: bool = False
    broker: str = "localhost"
    port: int = DEFAULT_MQTT_PORT
    username: str = ""
    password: str = ""
    client_id: str = "shareomat"
    topic_prefix: str = DEFAULT_TOPIC_PREFIX
    discovery_prefix: str = "homeassistant"
    discovery_enabled: bool = True
    command_topic_enabled: bool = True
    qos: int = 1
    retain: bool = True
    tls_enabled: bool = False
    tls_ca_cert: str = ""   # Path to CA certificate file, empty = use system CAs
    energy_data_ttl_seconds: int = 21600   # staleness cutoff for shareomat/energy_data/* (6h default)


@dataclass
class EmailConfig:
    """IMAP mailbox polling for automatic meter-data ingestion (e.g. a dedicated Gmail inbox)."""

    enabled: bool = False
    imap_host: str = "imap.gmail.com"
    imap_port: int = DEFAULT_IMAP_PORT
    username: str = ""
    password: str = ""             # Gmail: use an App Password, never the account password
    folder: str = "INBOX"
    allowed_senders: list[str] = field(default_factory=list)  # empty = accept any sender
    poll_interval_seconds: int = 300


@dataclass
class WebConfig:
    """Configuration for the Shareomat admin web server (standalone or behind HA Ingress)."""

    enabled: bool = True
    port: int = 8099


@dataclass
class RuntimeConfig:
    """The technical configuration that comes from Docker/Home-Assistant options, not SQLite."""

    paths: PathConfig
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    web: WebConfig = field(default_factory=WebConfig)


@dataclass
class LegConfig:
    """Full configuration for one LEG/ZEV (local energy community) settlement run.

    Built by shareomat.database.config_builder.build_leg_config() — never
    constructed directly from YAML. community/participants/meters/tariff
    come from SQLite; paths/processing/mqtt/email/web come from a
    RuntimeConfig combined with database-backed OperationSettings.
    """

    community: Community
    participants: list[Participant]
    meters: list[Meter]
    tariff: Tariff
    paths: PathConfig
    processing: ProcessingConfig
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    web: WebConfig = field(default_factory=WebConfig)


# ── Runtime (technical) config parsing ──────────────────────────────────────


def _parse_paths(raw: dict[str, Any]) -> PathConfig:
    p = raw["paths"]
    return PathConfig(
        inbox=Path(p["inbox"]),
        archive=Path(p["archive"]),
        reports=Path(p["reports"]),
        state=Path(p["state"]),
        share_inbox=str(p.get("share_inbox", "")),
    )


def _parse_mqtt(raw: dict[str, Any]) -> MqttConfig:
    m = raw.get("mqtt", {})
    if not m:
        return MqttConfig()
    return MqttConfig(
        enabled=bool(m.get("enabled", False)),
        broker=str(m.get("broker", "localhost")),
        port=int(m.get("port", DEFAULT_MQTT_PORT)),
        username=str(m.get("username", "")),
        password=str(m.get("password", "")),
        client_id=str(m.get("client_id", "shareomat")),
        topic_prefix=str(m.get("topic_prefix", DEFAULT_TOPIC_PREFIX)),
        discovery_prefix=str(m.get("discovery_prefix", "homeassistant")),
        discovery_enabled=bool(m.get("discovery_enabled", True)),
        command_topic_enabled=bool(m.get("command_topic_enabled", True)),
        qos=int(m.get("qos", 1)),
        retain=bool(m.get("retain", True)),
        tls_enabled=bool(m.get("tls_enabled", False)),
        tls_ca_cert=str(m.get("tls_ca_cert", "")),
        energy_data_ttl_seconds=int(m.get("energy_data_ttl_seconds", 21600)),
    )


def _parse_email(raw: dict[str, Any]) -> EmailConfig:
    e = raw.get("email", {})
    if not e:
        return EmailConfig()
    return EmailConfig(
        enabled=bool(e.get("enabled", False)),
        imap_host=str(e.get("imap_host", "imap.gmail.com")),
        imap_port=int(e.get("imap_port", DEFAULT_IMAP_PORT)),
        username=str(e.get("username", "")),
        password=str(e.get("password", "")),
        folder=str(e.get("folder", "INBOX")),
        allowed_senders=[str(s).lower() for s in e.get("allowed_senders", [])],
        poll_interval_seconds=int(e.get("poll_interval_seconds", 300)),
    )


def _parse_web(raw: dict[str, Any]) -> WebConfig:
    w = raw.get("web") or raw.get("ingress") or {}
    if not w:
        return WebConfig()
    return WebConfig(
        enabled=bool(w.get("enabled", True)),
        port=int(w.get("port", 8099)),
    )


def load_runtime_config(config_path: Path) -> RuntimeConfig:
    """Read the technical runtime configuration YAML (paths, MQTT, e-mail, web port)."""
    logger.info("Loading runtime config from %s", config_path)

    with config_path.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    return RuntimeConfig(
        paths=_parse_paths(raw),
        mqtt=_parse_mqtt(raw),
        email=_parse_email(raw),
        web=_parse_web(raw),
    )


# ── Full LegConfig validation ────────────────────────────────────────────────


def validate_leg_config(config: LegConfig) -> None:
    """Raise ValueError if the configuration violates LEG/ZEV rules (e.g. missing producer)."""
    errors: list[str] = []

    if not config.community.community_id:
        errors.append("Gemeinschaft ist nicht eingerichtet (fehlende community_id).")

    if not config.participants:
        errors.append("Keine Teilnehmer eingerichtet. Mindestens ein Teilnehmer ist erforderlich.")

    if not config.meters:
        errors.append("at least one meter is required")

    participant_ids = [p.participant_id for p in config.participants]
    dupes_p = {pid for pid in participant_ids if participant_ids.count(pid) > 1}
    if dupes_p:
        errors.append(
            f"Duplicate participant_id values found: {sorted(dupes_p)}. "
            "Each participant must have a unique ID."
        )

    meter_ids = [m.meter_id for m in config.meters]
    dupes_m = {mid for mid in meter_ids if meter_ids.count(mid) > 1}
    if dupes_m:
        errors.append(
            f"Duplicate meter_id values found: {sorted(dupes_m)}. "
            "Each meter must have a unique ID."
        )

    valid_participant_ids = set(participant_ids)
    for m in config.meters:
        if m.participant_id not in valid_participant_ids:
            errors.append(
                f"meter '{m.meter_id}' references unknown participant_id '{m.participant_id}'"
            )
        if m.role not in _VALID_METER_ROLES:
            errors.append(f"invalid role '{m.role}' for meter '{m.meter_id}'")

    for p in config.participants:
        if p.participant_type not in _VALID_PARTICIPANT_TYPES:
            errors.append(
                f"invalid participant_type '{p.participant_type}' for participant '{p.participant_id}'"
            )

    active_roles = {m.role for m in config.meters if m.active}
    producer_roles = {METER_ROLE_PRODUCER, METER_ROLE_PRODUCER_CONSUMER}
    consumer_roles = {METER_ROLE_CONSUMER, METER_ROLE_PRODUCER_CONSUMER}
    if not active_roles & producer_roles:
        errors.append("at least one active meter with role 'producer' or 'producer_consumer' is required")
    if not active_roles & consumer_roles:
        errors.append("at least one active meter with role 'consumer' or 'producer_consumer' is required")

    if config.processing.slot_minutes != SLOT_MINUTES:
        errors.append(
            f"slot_minutes must be {SLOT_MINUTES} for Swiss LEG/ZEV, "
            f"got {config.processing.slot_minutes}"
        )

    if config.processing.unknown_meter_policy not in _VALID_METER_POLICIES:
        errors.append(
            f"unknown_meter_policy must be one of {sorted(_VALID_METER_POLICIES)}, "
            f"got '{config.processing.unknown_meter_policy}'"
        )

    t = config.tariff
    for name, val in [
        ("local_rate_chf_kwh", t.local_rate_chf_kwh),
        ("grid_rate_chf_kwh", t.grid_rate_chf_kwh),
        ("feed_in_rate_chf_kwh", t.feed_in_rate_chf_kwh),
    ]:
        if val < 0:
            errors.append(f"tariff '{name}' must be >= 0, got {val}")

    for name, val in [("local_rate_chf_kwh", t.local_rate_chf_kwh), ("grid_rate_chf_kwh", t.grid_rate_chf_kwh)]:
        if val == 0:
            logger.warning("Tariff '%s' is 0.0 — all %s costs will be zero", name, name.replace("_chf_kwh", ""))

    if config.mqtt.enabled:
        if not (1 <= config.mqtt.port <= 65535):
            errors.append(f"mqtt.port must be between 1 and 65535, got {config.mqtt.port}")

    if config.mqtt.enabled and config.mqtt.tls_enabled and config.mqtt.port == 1883:
        logger.warning("TLS is enabled but port is 1883 — typical TLS port is 8883")

    if config.email.enabled:
        if not (1 <= config.email.imap_port <= 65535):
            errors.append(f"email.imap_port must be between 1 and 65535, got {config.email.imap_port}")
        if not config.email.imap_host:
            errors.append("email.imap_host is required when email.enabled is true")
        if not config.email.username:
            errors.append("email.username is required when email.enabled is true")

    cron = config.processing.cron_schedule
    if cron:
        try:
            from croniter import croniter
            if not croniter.is_valid(cron):
                errors.append(f"processing.cron_schedule '{cron}' is not a valid cron expression")
        except ImportError:
            pass  # croniter not installed — skip validation

    if errors:
        msg = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(msg)
