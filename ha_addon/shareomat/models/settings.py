# -*- coding: utf-8 -*-
"""
File: shareomat/models/settings.py

Purpose:
    Domain model for community-level operating settings (automation mode
    and processing rules) — the "Grundeinstellungen" managed in the
    Shareomat admin UI, as opposed to technical runtime configuration
    (MQTT, paths, ...) which stays in shareomat.config.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data container — no business logic, no I/O.
    auto_create_billing / auto_create_invoices / auto_send_invoices are
    stored and displayed already, but not yet acted on — invoicing is a
    placeholder feature in this phase (see SHAREOMAT_UMBAU_STRUKTUR.md §5).

    peak_start_hour/peak_end_hour/peak_weekdays_only define when a slot
    counts as Hochtarif (HT) for tariffs with rate_mode="ht_nt" (see
    shareomat.models.tariff) — used by shareomat.core.pipeline.tariff_time.
    Not every grid operator defines HT/NT the same way, so this is an
    editable setting, not a hardcoded rule. The defaults (Mon-Fri 06:00-22:00
    = HT) are a common Swiss starting convention, not a verified EBL rule —
    adjust once the community's actual grid-operator definition is known.

    The CSV path and its settings (meter data source, unknown meter policy,
    archiving, cron schedule, automatic inbox scan) were removed in 0.6.34
    (ADR-0012): billing always uses the grid operator's official SDAT split.
    Values stored by an older version stay in the settings table and are
    ignored.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OperationSettings:
    """Community-level automation mode and processing rules."""

    auto_create_billing: bool = False
    auto_create_invoices: bool = False
    auto_send_invoices: bool = False
    peak_start_hour: int = 6               # Hochtarif start hour (0-23), local time
    peak_end_hour: int = 22                # Hochtarif end hour (0-23, exclusive), local time
    peak_weekdays_only: bool = True        # True: Sat/Sun always Niedertarif
    # Write-once backup of the Swisseldex originals (ADR-0010): RAW_BACKUP_MODE_* from leg_const,
    # the network storage below /share (e.g. "omv") and the subfolder on it.
    raw_backup_mode: str = "off"
    raw_backup_mount: str = ""
    raw_backup_subdir: str = "swisseldex-rohdaten"


@dataclass(frozen=True)
class SwisseldexSchedule:
    """Automatic Swisseldex sync: on/off and the interval in hours.

    Switched on the "Externe Daten" page and effective at once, without a
    restart. Until it is saved there once, the add-on options
    swisseldex.auto_sync / sync_interval_hours apply (from_addon_options=True),
    so an installation that switched it on in the add-on keeps it after the
    update. Kept out of OperationSettings on purpose: saving the other
    settings writes every field, which would end the add-on fallback.
    """

    auto_sync: bool
    interval_hours: int
    from_addon_options: bool = False
