# -*- coding: utf-8 -*-
"""
File: shareomat/restore.py

Purpose:
    Command line to restore a backup ZIP ("Sicherung erstellen") without
    Home Assistant: python -m shareomat.restore <sicherung.zip> [--db PATH]

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Only a thin shell around shareomat.database.backup.restore_backup(), which
    does every check and never deletes or overwrites existing data (it is moved
    aside). Shareomat must be stopped while restoring.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from shareomat.database.backup import RestoreError, restore_backup
from shareomat.database.sqlite import resolve_db_path


def main(argv: list[str] | None = None) -> int:
    """Restore the given backup; 0 on success, 1 if it was refused (nothing changed then)."""
    parser = argparse.ArgumentParser(
        prog="python -m shareomat.restore",
        description="Shareomat-Sicherung wiederherstellen. Shareomat vorher beenden. Vorhandene Daten werden "
                    "nicht gelöscht, sondern in den Ordner vor-wiederherstellung-<Zeit> verschoben.",
    )
    parser.add_argument("backup", type=Path, help="die Sicherungsdatei (shareomat-sicherung-….zip)")
    parser.add_argument("--db", type=Path, default=None,
                        help="Pfad der Datenbank (Standard: SHAREOMAT_DB_PATH bzw. data/shareomat.db)")
    args = parser.parse_args(argv)
    db_path = args.db or resolve_db_path()
    try:
        result = restore_backup(args.backup, db_path)
    except RestoreError as exc:
        print(f"Wiederherstellung abgebrochen, nichts geändert: {exc}", file=sys.stderr)
        return 1
    print(f"Wiederhergestellt: {result.db_path}")
    print(f"  Sicherung vom {result.backup_created_at}, Schema {result.schema_version}, "
          f"{result.documents} Dokument(e), {result.sessions_revoked} Sitzung(en) beendet")
    if result.moved_aside is not None:
        print(f"  Der bisherige Stand liegt unverändert in {result.moved_aside}")
    print("  Shareomat jetzt starten; bei einem älteren Schema migriert der Start mit eigener Sicherung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
