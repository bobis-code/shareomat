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
