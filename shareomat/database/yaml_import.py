# -*- coding: utf-8 -*-
"""
File: shareomat/database/yaml_import.py

Purpose:
    One-time import of a pre-existing leg_config.yaml (community,
    participants, meters, tariffs) into SQLite, for installations that
    were set up before the admin database existed.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    The marker for "already imported" is simply that a community row
    exists — once import_legacy_yaml_if_empty() has run once (or a user
    has completed the setup wizard), it never runs again. The add-on
    options / leg_config.yaml are afterwards ignored for this data, per
    SHAREOMAT_UMBAU_STRUKTUR.md §13.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml

from shareomat.database.community import get_community, save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff

logger = logging.getLogger(__name__)


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return Decimal("0")


def import_legacy_yaml_if_empty(db_path: Path, yaml_path: Path) -> bool:
    """Import community/participants/meters/tariffs from `yaml_path` if the DB has none yet.

    Returns True if an import was performed, False if it was skipped
    (database already has a community, or the YAML file has no legacy
    admin data — e.g. a genuinely fresh installation).
    """
    if get_community(db_path) is not None:
        return False

    if not yaml_path.exists():
        return False

    with yaml_path.open(encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f) or {}

    leg = raw.get("leg") or {}
    participants_raw = raw.get("participants") or []
    if not leg and not participants_raw:
        logger.info("No legacy admin data found in %s — starting with an empty database.", yaml_path)
        return False

    logger.info("Importing legacy configuration from %s into %s", yaml_path, db_path)

    save_community(db_path, Community(
        community_id=str(leg.get("community_id", "")),
        name=str(leg.get("name", "")),
    ))

    for p in participants_raw:
        create_participant(db_path, Participant(
            participant_id=str(p["participant_id"]),
            label=str(p["label"]),
            participant_type=str(p["participant_type"]),
            active=bool(p.get("active", True)),
        ))

    for m in raw.get("meters") or []:
        create_meter(db_path, Meter(
            meter_id=str(m["meter_id"]),
            participant_id=str(m["participant_id"]),
            label=str(m["label"]),
            role=str(m["role"]),
            active=bool(m.get("active", True)),
        ))

    tariffs_raw = raw.get("tariffs")
    if tariffs_raw:
        create_tariff(db_path, Tariff(
            local_rate_chf_kwh=_decimal(tariffs_raw.get("local_rate_chf_kwh", 0)),
            grid_rate_chf_kwh=_decimal(tariffs_raw.get("grid_rate_chf_kwh", 0)),
            feed_in_rate_chf_kwh=_decimal(tariffs_raw.get("feed_in_rate_chf_kwh", 0)),
            name="Import",
            valid_from=date.today(),
        ))

    logger.info(
        "Legacy import complete: %d participant(s), %d meter(s), tariff=%s",
        len(participants_raw), len(raw.get("meters") or []), "yes" if tariffs_raw else "no",
    )
    return True
