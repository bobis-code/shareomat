# -*- coding: utf-8 -*-
"""
File: shareomat/ha/emsomat_install.py

Purpose:
    Detects whether the Emsomat integration is installed in the Home
    Assistant configuration folder next to Shareomat, and which version,
    by reading custom_components/emsomat/manifest.json.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Read-only on purpose. Shareomat could technically write into the
    folder (the add-on maps config:rw), but installing/updating Emsomat
    stays with HACS — two install paths would fight over versions.

    As an add-on the Home Assistant configuration folder is mounted at
    /config (add-on option `map: config:rw`). Standalone there is usually
    no such folder; the result then says config_dir_found=False.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from shareomat.models.integrations import EmsomatInstall

logger = logging.getLogger(__name__)

HA_CONFIG_DIR = Path("/config")
_MANIFEST_RELATIVE = Path("custom_components") / "emsomat" / "manifest.json"


# ── Private helpers ──────────────────────────────────────────────────────

def _read_version(manifest_path: Path) -> str | None:
    """The `version` field of the manifest, or None if it is unreadable or missing."""
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Emsomat manifest unreadable at %s: %s", manifest_path, exc)
        return None
    version = manifest.get("version") if isinstance(manifest, dict) else None
    return str(version) if version else None


# ── Public interface ─────────────────────────────────────────────────────

def read_emsomat_install(config_dir: Path = HA_CONFIG_DIR) -> EmsomatInstall:
    """Installation state of Emsomat below `config_dir` (the Home Assistant config folder)."""
    if not config_dir.is_dir():
        return EmsomatInstall(config_dir_found=False, installed=False)

    manifest_path = config_dir / _MANIFEST_RELATIVE
    if not manifest_path.is_file():
        return EmsomatInstall(config_dir_found=True, installed=False)

    return EmsomatInstall(config_dir_found=True, installed=True, version=_read_version(manifest_path))
