# -*- coding: utf-8 -*-
"""
File: shareomat/database/config_builder.py

Purpose:
    Combine the technical RuntimeConfig (paths/MQTT/e-mail/web, from
    Docker/HA options) with SQLite-backed master data (community,
    participants, meters, tariff, operating settings) into one LegConfig
    for the settlement core.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Called fresh before every settlement run (manual trigger, cron,
    watcher, ...) so edits made in the admin web UI take effect
    immediately — see main.py. The settlement core itself never touches
    SQLite or the database/ package.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Protocol

from shareomat.config import LegConfig, ProcessingConfig, RuntimeConfig
from shareomat.database.community import get_community
from shareomat.database.meters import list_meters
from shareomat.database.participants import list_participants
from shareomat.database.settings import get_operation_settings
from shareomat.database.tariffs import get_tariff_for_date
from shareomat.leg_const import SLOT_MINUTES


class IncompleteConfigError(Exception):
    """Raised when SQLite does not yet hold enough master data to build a LegConfig.

    Callers (main.py) catch this to show the setup wizard instead of a
    hard startup failure — an empty database is a normal, expected state
    for a fresh installation, not an error.
    """


class _HasValidity(Protocol):
    valid_from: date | None
    valid_until: date | None
    active: bool


def _is_effective(entity: _HasValidity, today: date) -> bool:
    """True if `entity` is active and today falls inside its validity window (if any)."""
    if not entity.active:
        return False
    if entity.valid_from is not None and entity.valid_from > today:
        return False
    if entity.valid_until is not None and entity.valid_until < today:
        return False
    return True


def build_leg_config(db_path: Path, runtime: RuntimeConfig, *, as_of: date | None = None) -> LegConfig:
    """Build a complete LegConfig from the database and runtime configuration.

    Raises IncompleteConfigError if the community, participants, meters,
    or a tariff valid for `as_of` (default: today) are not yet set up.
    """
    today = as_of or date.today()

    community = get_community(db_path)
    if community is None or not community.community_id:
        raise IncompleteConfigError("Keine Gemeinschaft eingerichtet.")

    participants = [p for p in list_participants(db_path) if _is_effective(p, today)]
    if not participants:
        raise IncompleteConfigError("Keine aktiven Teilnehmer eingerichtet.")

    meters = [m for m in list_meters(db_path) if _is_effective(m, today)]
    if not meters:
        raise IncompleteConfigError("Keine aktiven Messpunkte eingerichtet.")

    tariff = get_tariff_for_date(db_path, today)
    if tariff is None:
        raise IncompleteConfigError(f"Kein gültiger Tarif für {today.isoformat()} eingerichtet.")

    settings = get_operation_settings(db_path)
    processing = ProcessingConfig(
        slot_minutes=SLOT_MINUTES,
        archive_processed=settings.archive_processed,
        unknown_meter_policy=settings.unknown_meter_policy,
        cron_schedule=settings.cron_schedule,
        auto_scan_enabled=settings.auto_scan_enabled,
        scan_interval_seconds=settings.scan_interval_seconds,
        peak_start_hour=settings.peak_start_hour,
        peak_end_hour=settings.peak_end_hour,
        peak_weekdays_only=settings.peak_weekdays_only,
    )

    return LegConfig(
        community=community,
        participants=participants,
        meters=meters,
        tariff=tariff,
        paths=runtime.paths,
        processing=processing,
        mqtt=runtime.mqtt,
        email=runtime.email,
        web=runtime.web,
    )


@dataclass
class SetupStatus:
    """Which setup-wizard steps are already complete — drives the setup UI and dashboard hints."""

    has_community: bool
    has_participants: bool
    has_meters: bool
    has_tariff: bool

    @property
    def is_complete(self) -> bool:
        """True once every setup-wizard step has at least one row."""
        return self.has_community and self.has_participants and self.has_meters and self.has_tariff


def get_setup_status(db_path: Path, *, as_of: date | None = None) -> SetupStatus:
    """Report which master-data steps are missing, for the setup wizard and dashboard hints."""
    today = as_of or date.today()
    community = get_community(db_path)
    return SetupStatus(
        has_community=community is not None and bool(community.community_id),
        has_participants=any(_is_effective(p, today) for p in list_participants(db_path)),
        has_meters=any(_is_effective(m, today) for m in list_meters(db_path)),
        has_tariff=get_tariff_for_date(db_path, today) is not None,
    )
