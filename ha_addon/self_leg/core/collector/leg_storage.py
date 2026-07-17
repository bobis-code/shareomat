# -*- coding: utf-8 -*-
"""
File: self_leg/core/leg_storage.py

Purpose:
    Persistent state management for the SELF LEG engine.
    Tracks processed files by SHA-256 to prevent reprocessing,
    with rich metadata for auditability.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    State is written atomically via a temp-file + os.replace pattern
    to prevent JSON corruption on crash or container shutdown.
    Old single-hash format (schema_version 0) is migrated automatically.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_FILENAME = "processed_files.json"
_SCHEMA_VERSION = 1


@dataclass
class ProcessedEntry:
    sha256: str
    filename: str
    processed_at: str              # UTC ISO string
    report_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert this entry to a plain dict for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> ProcessedEntry:
        """Deserialize a processed entry from a plain dict."""
        return cls(
            sha256=d["sha256"],
            filename=d.get("filename", ""),
            processed_at=d.get("processed_at", ""),
            report_files=d.get("report_files", []),
        )


def _state_path(state_dir: Path) -> Path:
    """Return the full path to the state JSON file."""
    return state_dir / _STATE_FILENAME


def _load_entries(state_dir: Path) -> list[ProcessedEntry]:
    """Read all processed-file records from disk, migrating old formats if needed."""
    path = _state_path(state_dir)
    if not path.exists():
        return []
    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        # Migrate legacy format (schema_version 0: plain sha256 list)
        if "processed_sha256" in data:
            logger.info("Migrating state file to schema v%d", _SCHEMA_VERSION)
            return [
                ProcessedEntry(sha256=s, filename="", processed_at="", report_files=[])
                for s in data["processed_sha256"]
            ]

        return [ProcessedEntry.from_dict(e) for e in data.get("entries", [])]
    except (json.JSONDecodeError, KeyError) as exc:
        logger.warning("State file unreadable, starting fresh: %s", exc)
        return []


def _save_entries(state_dir: Path, entries: list[ProcessedEntry]) -> None:
    """Write processed-file records to disk atomically (temp file + rename)."""
    state_dir.mkdir(parents=True, exist_ok=True)
    target = _state_path(state_dir)
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "entries": [e.to_dict() for e in entries],
    }
    fd, tmp_path = tempfile.mkstemp(dir=state_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, target)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    logger.debug("State saved: %d processed file(s)", len(entries))


def is_processed(state_dir: Path, sha256: str) -> bool:
    """Return True if this file was already processed in a previous run."""
    return any(e.sha256 == sha256 for e in _load_entries(state_dir))


def mark_processed(
    state_dir: Path,
    sha256: str,
    filename: str,
    report_files: list[str] | None = None,
) -> None:
    """Record a processed inbox file so it is skipped on the next run."""
    entries = _load_entries(state_dir)
    entries.append(ProcessedEntry(
        sha256=sha256,
        filename=filename,
        processed_at=datetime.now(timezone.utc).isoformat(),
        report_files=list(report_files) if report_files else [],
    ))
    _save_entries(state_dir, entries)
