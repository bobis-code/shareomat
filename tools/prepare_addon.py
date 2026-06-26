# -*- coding: utf-8 -*-
"""
Prepare the Home Assistant add-on build directory.

The canonical application source lives in the project root:
  - self_leg/
  - main.py

The Home Assistant Supervisor builds add-ons from the add-on directory as its
Docker context. This script copies the canonical source into ha_addon/ so that
the add-on remains buildable without manually editing duplicated files.
"""

from __future__ import annotations

import argparse
import filecmp
import shutil
import sys
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "ha_addon"

SYNC_PATHS = (
    (ROOT / "self_leg", ADDON / "self_leg"),
    (ROOT / "main.py", ADDON / "main.py"),
)

IGNORE_PATTERNS = ("__pycache__", "*.pyc", "*.pyo")


def _iter_files(base: Path) -> set[Path]:
    """Return comparable files below base, excluding generated Python artifacts."""
    if base.is_file():
        return {Path(base.name)}

    result: set[Path] = set()
    for path in base.rglob("*"):
        if _is_ignored(path):
            continue
        if path.is_file():
            result.add(path.relative_to(base))
    return result


def _is_ignored(path: Path) -> bool:
    """Return True for generated files omitted from both checks and copies."""
    return (
        "__pycache__" in path.parts
        or path.suffix in {".pyc", ".pyo"}
    )


def _compare_tree(source: Path, target: Path) -> list[str]:
    """Return human-readable sync differences between source and target."""
    if not target.exists():
        return [f"missing: {target.relative_to(ROOT)}"]

    source_files = _iter_files(source)
    target_files = _iter_files(target)
    messages: list[str] = []

    for rel in sorted(source_files - target_files):
        messages.append(f"missing in add-on: {target.relative_to(ROOT) / rel}")

    for rel in sorted(target_files - source_files):
        messages.append(f"extra in add-on: {target.relative_to(ROOT) / rel}")

    for rel in sorted(source_files & target_files):
        source_file = source if source.is_file() else source / rel
        target_file = target if target.is_file() else target / rel
        if not filecmp.cmp(source_file, target_file, shallow=False):
            messages.append(f"out of sync: {target_file.relative_to(ROOT)}")

    return messages


def check() -> int:
    """Verify that ha_addon contains the current canonical source copy."""
    messages: list[str] = []
    for source, target in SYNC_PATHS:
        messages.extend(_compare_tree(source, target))

    if messages:
        print("Add-on source is not synced with canonical project source:")
        for message in messages:
            print(f"  - {message}")
        print("\nRun: python tools/prepare_addon.py")
        return 1

    print("Add-on source is synced.")
    return 0


def _remove_path(path: Path) -> None:
    """Remove a file or directory if it exists."""
    if not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_source(source: Path, target: Path) -> None:
    """Copy source to target, applying the same ignore policy used by checks."""
    if source.is_dir():
        shutil.copytree(
            source,
            target,
            ignore=shutil.ignore_patterns(*IGNORE_PATTERNS),
        )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _replace_path(source: Path, target: Path) -> None:
    """Atomically-ish replace target with a prepared copy of source.

    The new copy is built next to the target first. Only after the copy succeeds
    do we move the old target aside and promote the prepared copy.
    """
    temp = target.with_name(f".{target.name}.tmp-{uuid.uuid4().hex}")
    backup = target.with_name(f".{target.name}.bak-{uuid.uuid4().hex}")

    _remove_path(temp)
    _copy_source(source, temp)

    backup_created = False
    try:
        if target.exists():
            target.rename(backup)
            backup_created = True
        temp.rename(target)
    except Exception:
        if backup_created and backup.exists():
            backup.rename(target)
        raise
    finally:
        _remove_path(temp)
        _remove_path(backup)


def sync() -> int:
    """Refresh the add-on build copy from canonical project sources."""
    ADDON.mkdir(parents=True, exist_ok=True)

    for source, target in SYNC_PATHS:
        if not source.exists():
            print(f"ERROR: source path does not exist: {source}", file=sys.stderr)
            return 1

        _replace_path(source, target)
        print(f"Synced {source.relative_to(ROOT)} -> {target.relative_to(ROOT)}")

    return check()


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare or verify the HA add-on source copy.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify that ha_addon/ contains the current canonical source.",
    )
    args = parser.parse_args()
    return check() if args.check else sync()


if __name__ == "__main__":
    raise SystemExit(main())
