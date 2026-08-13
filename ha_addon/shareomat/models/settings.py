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

    meter_data_source: which channel the community currently receives meter
    data through — METER_DATA_SOURCE_* from shareomat.leg_const. "email_csv"
    (default) is today's flow: CSV/xlsx attachments via the email/share
    importers. "sdat_leg" switches "sdat"-typed inbox files to the real
    VSE SDAT-CH-2025 E31/E66 parser (shareomat.core.pipeline.raw.sdat_ch)
    instead of the legacy experimental one in leg_parser.py — see
    docs/sdat_leg_import.md. sdat_ch.py currently raises
    SdatChNotImplementedError until its XML structure is verified against
    official XSDs, so switching this on ahead of that is a deliberate,
    visible no-op rather than a silent misparse.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OperationSettings:
    """Community-level automation mode and processing rules."""

    unknown_meter_policy: str = "fail"     # UNKNOWN_METER_POLICY_* from shareomat.leg_const
    archive_processed: bool = True
    cron_schedule: str = ""                # cron expression, empty = disabled
    auto_scan_enabled: bool = False
    scan_interval_seconds: int = 60
    auto_create_billing: bool = False
    auto_create_invoices: bool = False
    auto_send_invoices: bool = False
    peak_start_hour: int = 6               # Hochtarif start hour (0-23), local time
    peak_end_hour: int = 22                # Hochtarif end hour (0-23, exclusive), local time
    peak_weekdays_only: bool = True        # True: Sat/Sun always Niedertarif
    meter_data_source: str = "email_csv"   # METER_DATA_SOURCE_* from shareomat.leg_const
