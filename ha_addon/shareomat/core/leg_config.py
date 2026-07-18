# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_config.py

Purpose:
    YAML configuration loader and validator for the Shareomat engine.
    Parses leg_config.yaml into typed dataclasses and enforces
    Swiss LEG/ZEV constraints at startup.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Validation raises ValueError on first detected group of errors
    so the engine never starts with a broken configuration.
    MQTT section is fully optional — absent or disabled means no MQTT.

    Identity model:
        participant_id  — business/billing identity (house, flat, tenant)
        meter_id        — real technical meter ID from EBL/grid operator
        One participant may have one or more meters.
        One meter belongs to exactly one participant.
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
    DEFAULT_SLOT_MINUTES,
    DEFAULT_TOPIC_PREFIX,
    METER_ROLE_CONSUMER,
    METER_ROLE_GRID,
    METER_ROLE_PRODUCER,
    METER_ROLE_PRODUCER_CONSUMER,
    PARTICIPANT_TYPE_CONSUMER,
    PARTICIPANT_TYPE_PRODUCER,
    PARTICIPANT_TYPE_PRODUCER_CONSUMER,
    SLOT_MINUTES,
    UNKNOWN_METER_POLICY_FAIL,
    UNKNOWN_METER_POLICY_SKIP,
)

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
}

_VALID_METER_POLICIES = {UNKNOWN_METER_POLICY_FAIL, UNKNOWN_METER_POLICY_SKIP}


# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class ParticipantConfig:
    """One billing participant in the LEG/ZEV community (house, flat, or tenant)."""

    participant_id: str
    label: str
    participant_type: str  # PARTICIPANT_TYPE_*
    active: bool = True


@dataclass
class MeterConfig:
    """One official grid/operator meter mapped to a participant."""

    meter_id: str
    participant_id: str
    label: str
    role: str  # METER_ROLE_*
    active: bool = True


@dataclass
class TariffConfig:
    """Settlement tariff rates in CHF per kWh."""

    local_rate_chf_kwh: float
    grid_rate_chf_kwh: float
    feed_in_rate_chf_kwh: float


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
    """Controls slot width (15 min) and whether processed files are moved to archive."""

    slot_minutes: int = DEFAULT_SLOT_MINUTES
    archive_processed: bool = True
    unknown_meter_policy: str = UNKNOWN_METER_POLICY_FAIL
    cron_schedule: str = ""     # cron expression, empty = disabled. Example: "0 6 * * *"
    auto_scan_enabled: bool = False
    scan_interval_seconds: int = 60


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
class IngressConfig:
    """Configuration for the optional Home Assistant Ingress web interface."""
    enabled: bool = False
    port: int = 8099


@dataclass
class LegConfig:
    """Full configuration for one LEG/ZEV (local energy community) settlement run."""

    community_id: str
    name: str
    participants: list[ParticipantConfig]
    meters: list[MeterConfig]
    tariffs: TariffConfig
    paths: PathConfig
    processing: ProcessingConfig
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    ingress: IngressConfig = field(default_factory=IngressConfig)


# ── Public functions ──────────────────────────────────────────────────────────


def parse_leg_section(raw: dict[str, Any]) -> tuple[str, str]:
    """Parse the community identity fields (ID and display name) from the config file."""
    leg = raw["leg"]
    return str(leg["community_id"]), str(leg["name"])


def parse_participants(raw: dict[str, Any]) -> list[ParticipantConfig]:
    """Parse the list of billing participants from the config file."""
    return [
        ParticipantConfig(
            participant_id=str(p["participant_id"]),
            label=str(p["label"]),
            participant_type=str(p["participant_type"]),
            active=bool(p.get("active", True)),
        )
        for p in raw.get("participants", [])
    ]


def parse_meters(raw: dict[str, Any]) -> list[MeterConfig]:
    """Parse the list of official grid meters from the config file."""
    return [
        MeterConfig(
            meter_id=str(m["meter_id"]),
            participant_id=str(m["participant_id"]),
            label=str(m["label"]),
            role=str(m["role"]),
            active=bool(m.get("active", True)),
        )
        for m in raw.get("meters", [])
    ]


def parse_tariffs(raw: dict[str, Any]) -> TariffConfig:
    """Parse tariff values."""
    t = raw["tariffs"]
    return TariffConfig(
        local_rate_chf_kwh=float(t["local_rate_chf_kwh"]),
        grid_rate_chf_kwh=float(t["grid_rate_chf_kwh"]),
        feed_in_rate_chf_kwh=float(t["feed_in_rate_chf_kwh"]),
    )


def parse_paths(raw: dict[str, Any]) -> PathConfig:
    """Parse runtime paths."""
    p = raw["paths"]
    return PathConfig(
        inbox=Path(p["inbox"]),
        archive=Path(p["archive"]),
        reports=Path(p["reports"]),
        state=Path(p["state"]),
        share_inbox=str(p.get("share_inbox", "")),
    )


def parse_processing(raw: dict[str, Any]) -> ProcessingConfig:
    """Parse processing settings."""
    proc = raw.get("processing", {})
    return ProcessingConfig(
        slot_minutes=int(proc.get("slot_minutes", DEFAULT_SLOT_MINUTES)),
        archive_processed=bool(proc.get("archive_processed", True)),
        unknown_meter_policy=str(proc.get("unknown_meter_policy", UNKNOWN_METER_POLICY_FAIL)),
        cron_schedule=str(proc.get("cron_schedule", "")),
        auto_scan_enabled=bool(proc.get("auto_scan_enabled", False)),
        scan_interval_seconds=int(proc.get("scan_interval_seconds", 60)),
    )


def parse_mqtt(raw: dict[str, Any]) -> MqttConfig:
    """Parse optional MQTT settings."""
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
    )


def parse_email(raw: dict[str, Any]) -> EmailConfig:
    """Parse optional IMAP email-import settings."""
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


def parse_ingress(raw: dict[str, Any]) -> IngressConfig:
    """Parse optional ingress settings."""
    ig = raw.get("ingress", {})
    if not ig:
        return IngressConfig()
    return IngressConfig(
        enabled=bool(ig.get("enabled", False)),
        port=int(ig.get("port", 8099)),
    )


def validate_config(config: LegConfig) -> None:
    """Raise ValueError if the configuration violates LEG/ZEV rules (e.g. missing producer)."""
    errors: list[str] = []

    if not config.participants:
        errors.append(
            "No participants configured. Add at least one entry under 'participants:' in leg_config.yaml"
        )

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

    t = config.tariffs
    for name, val in [
        ("local_rate_chf_kwh", t.local_rate_chf_kwh),
        ("grid_rate_chf_kwh", t.grid_rate_chf_kwh),
        ("feed_in_rate_chf_kwh", t.feed_in_rate_chf_kwh),
    ]:
        if val < 0:
            errors.append(f"tariff '{name}' must be >= 0, got {val}")

    # Tariff zero-rate warnings (suspicious but not invalid)
    for name, val in [("local_rate_chf_kwh", t.local_rate_chf_kwh), ("grid_rate_chf_kwh", t.grid_rate_chf_kwh)]:
        if val == 0:
            logger.warning("Tariff '%s' is 0.0 — all %s costs will be zero", name, name.replace("_chf_kwh", ""))

    # MQTT port range validation
    if config.mqtt.enabled:
        if not (1 <= config.mqtt.port <= 65535):
            errors.append(f"mqtt.port must be between 1 and 65535, got {config.mqtt.port}")

    # TLS port warning
    if config.mqtt.enabled and config.mqtt.tls_enabled and config.mqtt.port == 1883:
        logger.warning("TLS is enabled but port is 1883 — typical TLS port is 8883")

    # Email (IMAP) validation
    if config.email.enabled:
        if not (1 <= config.email.imap_port <= 65535):
            errors.append(f"email.imap_port must be between 1 and 65535, got {config.email.imap_port}")
        if not config.email.imap_host:
            errors.append("email.imap_host is required when email.enabled is true")
        if not config.email.username:
            errors.append("email.username is required when email.enabled is true")

    # Cron schedule validation
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


def load_config(config_path: Path) -> LegConfig:
    """Read, parse, and validate the YAML config file; raise on any error."""
    logger.info("Loading config from %s", config_path)

    with config_path.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    community_id, name = parse_leg_section(raw)
    config = LegConfig(
        community_id=community_id,
        name=name,
        participants=parse_participants(raw),
        meters=parse_meters(raw),
        tariffs=parse_tariffs(raw),
        paths=parse_paths(raw),
        processing=parse_processing(raw),
        mqtt=parse_mqtt(raw),
        email=parse_email(raw),
        ingress=parse_ingress(raw),
    )

    validate_config(config)
    active_meters = sum(1 for m in config.meters if m.active)
    logger.info(
        "Config OK: %d participant(s), %d active meter(s), slot=%dmin, MQTT=%s, Email=%s",
        len(config.participants), active_meters, config.processing.slot_minutes,
        "enabled" if config.mqtt.enabled else "disabled",
        "enabled" if config.email.enabled else "disabled",
    )
    return config
