# -*- coding: utf-8 -*-
"""
File: shareomat/core/leg_import.py

Purpose:
    Inbox scanning, SHA-256 fingerprinting, and archive management
    for the Shareomat settlement pipeline.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Handles only file discovery and movement.
    Parsing is delegated to leg_parser.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path

from shareomat.leg_const import FILE_EXT_CSV, FILE_EXT_SDAT, FILE_EXT_XLSX, FILE_EXT_XML
from shareomat.models.meter import ImportFile

logger = logging.getLogger(__name__)

_SUPPORTED = {FILE_EXT_CSV, FILE_EXT_XML, FILE_EXT_SDAT, FILE_EXT_XLSX}


def _sha256(path: Path) -> str:
    """Compute the SHA-256 fingerprint of a file for duplicate detection."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _file_type(path: Path) -> str:
    """Map a file extension (.csv, .xml, .sdat, .xlsx) to its logical parser type."""
    suffix = path.suffix.lower()
    if suffix == FILE_EXT_CSV:
        return "csv"
    if suffix in (FILE_EXT_XML, FILE_EXT_SDAT):
        return "sdat"
    if suffix == FILE_EXT_XLSX:
        return "xlsx"
    raise ValueError(f"Unsupported extension: {suffix}")


def scan_inbox(inbox: Path) -> list[ImportFile]:
    """Collect all supported meter data files from the inbox folder, sorted by name."""
    files: list[ImportFile] = []
    for path in sorted(inbox.iterdir()):
        if not path.is_file() or path.suffix.lower() not in _SUPPORTED:
            continue
        try:
            files.append(ImportFile(
                path=path,
                file_type=_file_type(path),
                sha256=_sha256(path),
            ))
        except Exception as exc:
            logger.warning("Skipping %s: %s", path.name, exc)
    logger.info("Found %d file(s) in inbox %s", len(files), inbox)
    return files


def move_to_archive(source: Path, archive: Path) -> Path:
    """Move a processed inbox file to the archive folder without overwriting existing files."""
    archive.mkdir(parents=True, exist_ok=True)
    dest = archive / source.name
    if dest.exists():
        dest = archive / f"{source.stem}_{source.stat().st_mtime_ns}{source.suffix}"
    shutil.move(str(source), dest)
    logger.info("Archived %s -> %s", source.name, dest.name)
    return dest
