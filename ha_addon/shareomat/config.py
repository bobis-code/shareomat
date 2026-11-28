# -*- coding: utf-8 -*-
"""
File: shareomat/config.py

Purpose:
    Technical runtime configuration (paths, MQTT, e-mail import and
    sending, web server port) and the fully assembled LegConfig the settlement core
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
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import yaml

from shareomat.leg_const import (
    DEFAULT_IMAP_PORT,
    DEFAULT_MQTT_PORT,
    DEFAULT_TOPIC_PREFIX,
    METER_ROLE_CONSUMER,
    METER_ROLE_GRID,
    METER_ROLE_PRODUCER,
    METER_ROLE_PRODUCER_CONSUMER,
    METER_ROLE_STORAGE,
    SLOT_MINUTES,
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
    METER_ROLE_STORAGE,
    METER_ROLE_GRID,
}

# ── Dataclasses ───────────────────────────────────────────────────────────────


@dataclass
class PathConfig:
    """Runtime filesystem paths for the settlement engine."""

    inbox: Path
    archive: Path
    reports: Path
    state: Path
    share_inbox: str = ""  # Not used since 0.6.34 (CSV path removed, ADR-0012); add-on option kept until the new configuration (P7)
    backups: Path | None = None  # "Sicherung erstellen" target; None = <state>/backups


@dataclass
class ProcessingConfig:
    """Resolved processing rules for one settlement run.

    slot_minutes is a fixed Swiss LEG/ZEV constant; every other field is
    populated from the database-backed OperationSettings by
    shareomat.database.config_builder.build_leg_config(). The CSV-run fields
    (archiving, unknown meter policy, cron, inbox scan, meter data source)
    were removed in 0.6.34 (ADR-0012).
    """

    slot_minutes: int = SLOT_MINUTES
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
    password: str = field(default="", repr=False)
    client_id: str = "shareomat"
    topic_prefix: str = DEFAULT_TOPIC_PREFIX
    discovery_prefix: str = "homeassistant"
    discovery_enabled: bool = True
    command_topic_enabled: bool = True
    qos: int = 1
    retain: bool = True
    tls_enabled: bool = False
    tls_ca_cert: str = ""   # Path to CA certificate file, empty = use system CAs
    energy_data_ttl_seconds: int = 21600   # staleness cutoff (ValidUntil) for the Sparkplug Coordinator NCMD envelopes (LEG/DemandForecast, LEG/ExportPrice, LEG/FeedInPrice), 6h default


@dataclass
class EmailConfig:
    """IMAP mailbox polling for meter-data files. Not used since 0.6.34 (CSV path removed, ADR-0012).

    The add-on options email_* stay readable until the config interface (P3),
    so an existing add-on configuration keeps loading.
    """

    enabled: bool = False
    imap_host: str = "imap.gmail.com"
    imap_port: int = DEFAULT_IMAP_PORT
    username: str = ""
    password: str = field(default="", repr=False)   # Gmail: an App Password, never the account password
    folder: str = "INBOX"
    allowed_senders: list[str] = field(default_factory=list)  # empty = accept any sender
    poll_interval_seconds: int = 300


@dataclass
class WebConfig:
    """Configuration for the Shareomat admin web server (standalone or behind HA Ingress)."""

    enabled: bool = True
    port: int = 8099
    # Proxies whose X-Forwarded-Proto/-Host headers are believed (e.g. a local
    # HTTPS reverse proxy). The Home Assistant supervisor (172.30.32.2) is
    # always trusted for these. Never used to authenticate anybody.
    trusted_proxies: list[str] = field(default_factory=list)
    # Add-on option "reset_login": a fresh one-time code in the log allows
    # setting a new password (forgotten password). Harmless if left on.
    reset_login: bool = False


@dataclass
class SparkplugConfig:
    """Sparkplug B settings for the local Emsomat<->Shareomat channel, see
    docs/Architektur/Emsomat_Shareomat_MQTT_Vertrag.md. Uses the same broker
    as `mqtt` above (broker/port/username/password/tls_*), but its own MQTT
    client connection - Sparkplug's STATE Will and `mqtt.status`'s own Will
    can't share one connection (MQTT allows only one Will per connection)."""

    enabled: bool = False
    group_id: str = "emsomat"
    primary_host_id: str = "shareomat"
    emsomat_edge_node_id: str = ""
    participant_edge_node_id: str = "shareomat-relay"


@dataclass
class WanConfig:
    """Cross-House Sparkplug B settings (Shareomat<->Shareomat over the
    central relay), see docs/Architektur/Shareomat_CrossHouse_Sparkplug_Vertrag.md.
    Own broker connection details - deliberately separate from `mqtt`/
    `sparkplug` above (different broker: the central `mqtt.shareomat.ch`,
    not the local house broker).

    group_id here is the LEG's stable internal ID (Abschnitt 6.1 - candidate:
    Community.community_id, NOT a human-readable name), NOT the same value as
    the local sparkplug.group_id above (that one is per-house/local, this one
    is per-LEG/shared across houses).

    own_participant_id identifies which Participant (see shareomat.models.participant)
    this Shareomat instance's own, locally-connected Emsomat represents on the
    WAN - one LegConfig can list many participants (a whole LEG's billing),
    but the local Sparkplug channel only ever describes ONE site's own
    Emsomat. No new ID scheme: reuses the existing participant_id namespace,
    just points at which one is "this site"."""

    enabled: bool = False
    broker: str = ""
    port: int = 8883
    username: str = ""
    password: str = field(default="", repr=False)
    tls_enabled: bool = True
    tls_ca_cert: str = ""
    group_id: str = ""
    edge_node_id: str = ""
    own_participant_id: str = ""


@dataclass
class SwisseldexConfig:
    """Swisseldex Datahub mailbox ("Server beim Datahub"), fetched over FTPES.

    Credentials live here, in the technical runtime configuration (add-on
    options / config.yaml), never in SQLite - the same place as the MQTT and
    e-mail passwords. Off by default; automatic sync is a second, separate
    switch. See docs/development/Swisseldex-SDAT-Auftrag_2026-11-12.md.
    """

    enabled: bool = False
    host: str = "datahub.swisseldex.ch"
    port: int = 21
    username: str = ""
    password: str = ""
    auto_sync: bool = False
    sync_interval_hours: int = 6
    timeout_seconds: int = 30

    def __repr__(self) -> str:
        # The password must never reach a log line through an accidental repr().
        return (
            f"SwisseldexConfig(enabled={self.enabled}, host={self.host!r}, port={self.port}, "
            f"username={'set' if self.username else 'empty'}, password={'set' if self.password else 'empty'}, "
            f"auto_sync={self.auto_sync}, sync_interval_hours={self.sync_interval_hours})"
        )


SWISSELDEX_SYNC_INTERVALS_HOURS: tuple[int, ...] = (1, 3, 6, 12, 24)

SMTP_SECURITY_SSL = "ssl"            # implicit TLS from the first byte (port 465)
SMTP_SECURITY_STARTTLS = "starttls"  # plain connect, then STARTTLS before anything else (port 587)
SMTP_SECURITY_MODES: tuple[str, ...] = (SMTP_SECURITY_SSL, SMTP_SECURITY_STARTTLS)


@dataclass
class SmtpConfig:
    """Outgoing mailbox for sending documents (Rechnung, Gutschrift, ...) by e-mail.

    Like the IMAP and Swisseldex credentials, this lives in the technical
    runtime configuration (add-on options), never in SQLite. There is no
    unencrypted mode: only implicit TLS or STARTTLS, always with certificate
    verification. A wrong or incomplete section never stops the start; it
    only keeps sending disabled (see smtp_problems()).
    """

    enabled: bool = False
    host: str = "smtp.gmail.com"
    port: int = 465
    security: str = SMTP_SECURITY_SSL
    username: str = ""
    password: str = ""             # Gmail: an App Password, never the account password
    from_address: str = ""
    from_name: str = ""
    timeout_seconds: int = 30

    def __repr__(self) -> str:
        # The password must never reach a log line through an accidental repr().
        return (
            f"SmtpConfig(enabled={self.enabled}, host={self.host!r}, port={self.port}, "
            f"security={self.security!r}, username={'set' if self.username else 'empty'}, "
            f"password={'set' if self.password else 'empty'}, from_address={'set' if self.from_address else 'empty'})"
        )


def smtp_problems(smtp: SmtpConfig) -> list[str]:
    """Why sending is not possible with this configuration (German, for the operator); empty = ready."""
    if not smtp.enabled:
        return ["Der E-Mail-Versand ist ausgeschaltet (Add-on-Konfiguration → smtp → enabled)."]
    problems = []
    if not smtp.host:
        problems.append("Kein Mailserver (smtp → host) eingetragen.")
    if not (1 <= smtp.port <= 65535):
        problems.append(f"Ungültiger Port {smtp.port} (smtp → port).")
    if smtp.security not in SMTP_SECURITY_MODES:
        problems.append("Verschlüsselung muss «ssl» oder «starttls» sein (smtp → security).")
    if not smtp.username or not smtp.password:
        problems.append("Benutzername oder Passwort fehlt (smtp → username / password).")
    if not smtp.from_address:
        problems.append("Keine Absenderadresse eingetragen (smtp → from_address).")
    return problems


@dataclass
class EntsoeConfig:
    """ENTSO-E Transparency Platform token (day-ahead prices for Emsomat).

    Only set here when it comes from the secrets file (HA add-on option) or
    the environment; otherwise the web UI keeps it in the secret store
    (shareomat.secret_store). Never in the database since 0.6.35.
    """

    api_token: str = ""

    def __repr__(self) -> str:
        return f"EntsoeConfig(api_token={'set' if self.api_token else 'empty'})"


@dataclass
class RuntimeConfig:
    """The technical configuration that comes from Docker/Home-Assistant options, not SQLite."""

    paths: PathConfig
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    email: EmailConfig = field(default_factory=EmailConfig)
    web: WebConfig = field(default_factory=WebConfig)
    sparkplug: SparkplugConfig = field(default_factory=SparkplugConfig)
    wan: WanConfig = field(default_factory=WanConfig)
    swisseldex: SwisseldexConfig = field(default_factory=SwisseldexConfig)
    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    # Paperless-ngx base URL for evidence links (shareomat.core.paperless). Only
    # a link target for the operator's browser — Shareomat never calls it.
    paperless_url: str = ""
    entsoe: EntsoeConfig = field(default_factory=EntsoeConfig)
    # Where each secret that is set came from ("section.key" -> SECRET_SOURCE_*); names only, no values.
    secret_sources: dict[str, str] = field(default_factory=dict)


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
    sparkplug: SparkplugConfig = field(default_factory=SparkplugConfig)
    wan: WanConfig = field(default_factory=WanConfig)


# ── Runtime (technical) config parsing ──────────────────────────────────────


def _parse_paths(raw: dict[str, Any]) -> PathConfig:
    p = raw["paths"]
    return PathConfig(
        inbox=Path(p["inbox"]),
        archive=Path(p["archive"]),
        reports=Path(p["reports"]),
        state=Path(p["state"]),
        share_inbox=str(p.get("share_inbox", "")),
        backups=Path(p["backups"]) if p.get("backups") else None,
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
    proxies = w.get("trusted_proxies", [])
    if isinstance(proxies, str):
        proxies = [p.strip() for p in proxies.split(",") if p.strip()]
    return WebConfig(
        enabled=bool(w.get("enabled", True)),
        port=int(w.get("port", 8099)),
        trusted_proxies=[str(p) for p in proxies],
        reset_login=bool(w.get("reset_login", False)),
    )


def _parse_sparkplug(raw: dict[str, Any]) -> SparkplugConfig:
    """Parse optional Sparkplug B settings (Emsomat<->Shareomat channel)."""
    s = raw.get("sparkplug", {})
    if not s:
        return SparkplugConfig()
    return SparkplugConfig(
        enabled=bool(s.get("enabled", False)),
        group_id=str(s.get("group_id", "emsomat")),
        primary_host_id=str(s.get("primary_host_id", "shareomat")),
        emsomat_edge_node_id=str(s.get("emsomat_edge_node_id", "")),
        participant_edge_node_id=str(s.get("participant_edge_node_id", "shareomat-relay")),
    )


def _parse_wan(raw: dict[str, Any]) -> WanConfig:
    """Parse optional Cross-House Sparkplug B settings (Shareomat<->Shareomat)."""
    w = raw.get("wan", {})
    if not w:
        return WanConfig()
    return WanConfig(
        enabled=bool(w.get("enabled", False)),
        broker=str(w.get("broker", "")),
        port=int(w.get("port", 8883)),
        username=str(w.get("username", "")),
        password=str(w.get("password", "")),
        tls_enabled=bool(w.get("tls_enabled", True)),
        tls_ca_cert=str(w.get("tls_ca_cert", "")),
        group_id=str(w.get("group_id", "")),
        edge_node_id=str(w.get("edge_node_id", "")),
        own_participant_id=str(w.get("own_participant_id", "")),
    )


def _parse_swisseldex(raw: dict[str, Any]) -> SwisseldexConfig:
    """Parse the optional Swisseldex mailbox section. Missing section = off."""
    s = raw.get("swisseldex", {})
    if not s:
        return SwisseldexConfig()
    interval = int(s.get("sync_interval_hours", 6))
    if interval not in SWISSELDEX_SYNC_INTERVALS_HOURS:
        logger.warning(
            "swisseldex.sync_interval_hours=%s is not one of %s, using 6",
            interval, SWISSELDEX_SYNC_INTERVALS_HOURS,
        )
        interval = 6
    return SwisseldexConfig(
        enabled=bool(s.get("enabled", False)),
        host=str(s.get("host", "datahub.swisseldex.ch")).strip(),
        port=int(s.get("port", 21)),
        username=str(s.get("username", "")).strip(),
        password=str(s.get("password", "")),
        auto_sync=bool(s.get("auto_sync", False)),
        sync_interval_hours=interval,
        timeout_seconds=max(5, min(120, int(s.get("timeout_seconds", 30)))),
    )


def _parse_smtp(raw: dict[str, Any]) -> SmtpConfig:
    """Parse the optional outgoing mailbox. Missing or malformed section = off, never a failed start."""
    s = raw.get("smtp") or {}
    if not isinstance(s, dict):
        logger.error("smtp section ignored: not a mapping")
        return SmtpConfig()
    try:
        port = int(s.get("port", 465))
        timeout = max(5, min(120, int(s.get("timeout_seconds", 30))))
    except (TypeError, ValueError):
        logger.error("smtp.port/timeout_seconds is not a number: e-mail sending stays off")
        return SmtpConfig()
    return SmtpConfig(
        enabled=bool(s.get("enabled", False)),
        host=str(s.get("host", "smtp.gmail.com") or "").strip(),
        port=port,
        security=str(s.get("security", SMTP_SECURITY_SSL) or "").strip().lower(),
        username=str(s.get("username", "") or "").strip(),
        password=str(s.get("password", "") or ""),
        from_address=str(s.get("from_address", "") or "").strip(),
        from_name=str(s.get("from_name", "") or "").strip(),
        timeout_seconds=timeout,
    )


def _parse_paperless(raw: dict[str, Any]) -> str:
    """The validated Paperless base URL; an invalid one disables the links (logged), never the add-on."""
    from shareomat.core.paperless import PaperlessValueError, normalize_base_url

    value = str((raw.get("paperless") or {}).get("url", "") or "")
    try:
        return normalize_base_url(value)
    except PaperlessValueError as exc:
        logger.error("paperless.url ignored: %s", exc)
        return ""


# Every secret of the runtime configuration: (section, key) -> environment variable.
SECRET_FIELDS: dict[tuple[str, str], str] = {
    ("mqtt", "password"): "SHAREOMAT_MQTT_PASSWORD",
    ("email", "password"): "SHAREOMAT_EMAIL_PASSWORD",
    ("wan", "password"): "SHAREOMAT_WAN_PASSWORD",
    ("swisseldex", "password"): "SHAREOMAT_SWISSELDEX_PASSWORD",
    ("smtp", "password"): "SHAREOMAT_SMTP_PASSWORD",
    ("entsoe", "api_token"): "SHAREOMAT_ENTSOE_API_TOKEN",
}
SECRET_SOURCE_CONFIG_FILE = "config_file"     # in the configuration file itself (discouraged)
SECRET_SOURCE_SECRETS_FILE = "secrets_file"   # in the secrets file (HA add-on option, or by hand)
SECRET_SOURCE_ENVIRONMENT = "environment"
SECRETS_FILENAME = "secrets.yaml"


def secrets_file_path(config_path: Path, environ: Mapping[str, str] | None = None) -> Path:
    """SHAREOMAT_SECRETS_PATH, else `secrets.yaml` next to the configuration file."""
    override = (environ if environ is not None else os.environ).get("SHAREOMAT_SECRETS_PATH", "").strip()
    return Path(override) if override else Path(config_path).parent / SECRETS_FILENAME


def _apply_secrets(raw: dict[str, Any], secrets_path: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Merge the secrets file and the environment into `raw`, later wins: file < secrets file < environment.

    The secrets file holds only secrets; on Linux it is refused if other users
    can read it (the start fails with a clear message instead). Returns where
    each set secret came from - names only, never values.
    """
    from shareomat.secret_store import SecretStoreError, check_private_file

    sources: dict[str, str] = {}
    for (section, key) in SECRET_FIELDS:
        if str((raw.get(section) or {}).get(key) or ""):
            sources[f"{section}.{key}"] = SECRET_SOURCE_CONFIG_FILE
    if sources:
        logger.warning("Credentials in the configuration file (%s): better move them to %s",
                       ", ".join(sorted(sources)), secrets_path.name)
    if secrets_path.exists():
        check_private_file(secrets_path)
        try:
            secret_raw = yaml.safe_load(secrets_path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise SecretStoreError(f"{secrets_path} kann nicht gelesen werden: {type(exc).__name__}") from exc
        if not isinstance(secret_raw, dict):
            raise SecretStoreError(f"{secrets_path} hat kein gültiges Format.")
        unknown = sorted(f"{section}.{key}" for section, values in secret_raw.items() if isinstance(values, dict)
                         for key in values if (section, key) not in SECRET_FIELDS)
        if unknown:
            logger.warning("Unknown entries in %s ignored: %s", secrets_path.name, ", ".join(unknown))
        for (section, key) in SECRET_FIELDS:
            value = str(((secret_raw.get(section) or {}) if isinstance(secret_raw.get(section), dict) else {}).get(key) or "")
            if value:
                raw.setdefault(section, {})
                if raw[section] is None:
                    raw[section] = {}
                raw[section][key] = value
                sources[f"{section}.{key}"] = SECRET_SOURCE_SECRETS_FILE
    for (section, key), variable in SECRET_FIELDS.items():
        value = environ.get(variable, "")
        if value:
            raw.setdefault(section, {})
            if raw[section] is None:
                raw[section] = {}
            raw[section][key] = value
            sources[f"{section}.{key}"] = SECRET_SOURCE_ENVIRONMENT
    return sources


def load_runtime_config(config_path: Path, *, secrets_path: Path | None = None,
                        environ: Mapping[str, str] | None = None) -> RuntimeConfig:
    """Read the technical runtime configuration: file, then secrets file, then environment (ADR-0012 pt. 10).

    Raises shareomat.secret_store.SecretStoreError if the secrets file can't be
    used safely (readable by others, broken) - the start then shows the error.
    """
    logger.info("Loading runtime config from %s", config_path)
    environ = os.environ if environ is None else environ

    with config_path.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}
    sources = _apply_secrets(raw, secrets_path or secrets_file_path(config_path, environ), environ)

    return RuntimeConfig(
        paths=_parse_paths(raw),
        mqtt=_parse_mqtt(raw),
        email=_parse_email(raw),
        web=_parse_web(raw),
        sparkplug=_parse_sparkplug(raw),
        wan=_parse_wan(raw),
        swisseldex=_parse_swisseldex(raw),
        smtp=_parse_smtp(raw),
        paperless_url=_parse_paperless(raw),
        entsoe=EntsoeConfig(api_token=str((raw.get("entsoe") or {}).get("api_token") or "").strip()),
        secret_sources=sources,
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

    if config.sparkplug.enabled and not config.sparkplug.emsomat_edge_node_id.strip():
        errors.append(
            "sparkplug.enabled is true but sparkplug.emsomat_edge_node_id is empty - "
            "Shareomat needs Emsomat's node_id to know which Sparkplug topics to subscribe to"
        )

    if config.wan.enabled and not config.sparkplug.enabled:
        errors.append(
            "wan.enabled is true but sparkplug.enabled is false - the WAN Downlink needs a local "
            "ParticipantRelay and the WAN Uplink needs a local SparkplugHost to mirror from"
        )

    if config.wan.enabled:
        if not config.wan.broker.strip():
            errors.append("wan.enabled is true but wan.broker is empty")
        if not config.wan.group_id.strip():
            errors.append("wan.enabled is true but wan.group_id is empty - needed for LEG isolation")
        if not config.wan.edge_node_id.strip():
            errors.append("wan.enabled is true but wan.edge_node_id is empty - needed to identify this site on the WAN")
        if not config.wan.own_participant_id.strip():
            errors.append(
                "wan.enabled is true but wan.own_participant_id is empty - Shareomat needs to know "
                "which participant this site's own local Emsomat represents"
            )
        elif config.wan.own_participant_id not in participant_ids:
            errors.append(
                f"wan.own_participant_id '{config.wan.own_participant_id}' does not match any "
                "configured participant_id"
            )

    if errors:
        msg = "Configuration validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
        raise ValueError(msg)
