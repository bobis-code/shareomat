# -*- coding: utf-8 -*-
"""
File: shareomat/models/integrations.py

Purpose:
    Domain models for the "Apps & Integrationen" page: what Shareomat
    knows about companion apps installed next to it (currently Emsomat).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Pure data containers — no business logic, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmsomatInstall:
    """Installation state of the Emsomat Home Assistant integration.

    config_dir_found is False when Shareomat cannot see a Home Assistant
    configuration folder at all (standalone operation) — then `installed`
    says nothing about the real state and the page shows "unbekannt".
    """

    config_dir_found: bool
    installed: bool
    version: str | None = None
