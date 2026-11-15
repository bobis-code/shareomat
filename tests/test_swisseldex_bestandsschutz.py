# -*- coding: utf-8 -*-
"""Swisseldex (SX-1) leaves the existing installation alone.

Off: no thread, no directory, nothing registered. On: a sync with files
writes nothing to shareomat.db (business data, billing run, data_version),
not into the engine inbox, and the release of an existing billing draft
still works while it runs.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from dataclasses import replace

import main as app
from shareomat.config import SwisseldexConfig
from shareomat.core.collector.swisseldex_receiver import KIND_SYNC, SwisseldexReceiver
from shareomat.database import billing
from shareomat.web.state import get_state
from tests.swisseldex_fake import FakeMailbox, session_factory
from tests.test_billing_atomicity import case  # noqa: F401

_TABLES = (
    "communities", "participants", "meters", "tariffs", "contract_versions", "participant_contract",
    "billing_runs", "billing_records", "meter_readings", "settings", "data_imports", "audit_events",
)


def _business_snapshot(db) -> dict[str, list[tuple]]:
    with sqlite3.connect(db) as conn:
        return {table: conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall() for table in _TABLES}


def _receiver_threads() -> list[str]:
    return [t.name for t in threading.enumerate() if t.name == "swisseldex-receiver"]


def test_disabled_swisseldex_starts_nothing(case, monkeypatch):  # noqa: F811
    db, runtime, run_id, source = case
    assert runtime.swisseldex == SwisseldexConfig()
    before = set((runtime.paths.state).iterdir())
    assert app._setup_swisseldex(runtime, db) is None
    assert _receiver_threads() == []
    assert set(runtime.paths.state.iterdir()) == before
    assert get_state().swisseldex is None


def test_sync_with_files_leaves_business_data_and_inbox_untouched(case, tmp_path):  # noqa: F811
    db, runtime, run_id, source = case
    tables_before = _business_snapshot(db)
    db_bytes_before = hashlib.sha256(db.read_bytes()).hexdigest()
    inbox_before = sorted(p.name for p in runtime.paths.inbox.iterdir())

    box = FakeMailbox()
    box.put("E66_test.xml", b"<synthetic/>")
    box.put("E31_test.xml", b"<synthetic-aggregate/>")
    receiver = SwisseldexReceiver(
        SwisseldexConfig(enabled=True, username="u", password="p"), runtime.paths.state / "swisseldex",
        session_factory=session_factory(box, "u", "p"),
    )
    record = receiver.run_now(KIND_SYNC)
    assert record["downloaded"] == 2

    assert _business_snapshot(db) == tables_before
    assert hashlib.sha256(db.read_bytes()).hexdigest() == db_bytes_before
    assert sorted(p.name for p in runtime.paths.inbox.iterdir()) == inbox_before


def test_release_of_an_existing_draft_is_not_disturbed_by_a_sync(case, monkeypatch):  # noqa: F811
    """A release refuses when shareomat.db changes under it (data_version); a sync must not cause that."""
    db, runtime, run_id, source = case
    box = FakeMailbox()
    box.put("E66_test.xml", b"<synthetic/>")
    receiver = SwisseldexReceiver(
        SwisseldexConfig(enabled=True, username="u", password="p"), runtime.paths.state / "swisseldex",
        session_factory=session_factory(box, "u", "p"),
    )
    synced = []
    real_preview = billing.compute_billing_preview

    def preview_with_sync(*args, **kwargs):
        result = real_preview(*args, **kwargs)
        synced.append(receiver.run_now(KIND_SYNC))   # a sync exactly while the release recomputes
        return result

    monkeypatch.setattr(billing, "compute_billing_preview", preview_with_sync)
    released = billing.release_billing_run(db, run_id, runtime=runtime)
    assert released.status == "released"
    assert synced and synced[0]["downloaded"] == 1


def test_enabled_receiver_is_registered_started_and_stopped(case, monkeypatch):  # noqa: F811
    db, runtime, run_id, source = case
    runtime = replace(runtime, swisseldex=SwisseldexConfig(enabled=True, username="u", password="p"))
    receiver = app._setup_swisseldex(runtime, db)
    try:
        assert receiver is not None and _receiver_threads() == ["swisseldex-receiver"]
        assert (runtime.paths.state / "swisseldex" / "received").is_dir()
    finally:
        receiver.stop(timeout=5)
    assert _receiver_threads() == []


def test_default_source_sync_with_importer_does_not_disturb_a_release(case, monkeypatch):  # noqa: F811
    """With the default data source the automatic import is skipped: no write to shareomat.db during a release."""
    db, runtime, run_id, source = case
    from tests.sdat_fixtures import Document, Series, build
    box = FakeMailbox()
    box.put("E66.xml", build(Document(series=[Series("CH1000000000000000000000000000001", "consumption", "total",
                                                         ["1.000"] * 4)])))
    receiver = SwisseldexReceiver(
        SwisseldexConfig(enabled=True, username="u", password="p"), runtime.paths.state / "swisseldex",
        session_factory=session_factory(box, "u", "p"), importer=app._swisseldex_importer(db, runtime),
    )
    synced = []
    real_preview = billing.compute_billing_preview

    def preview_with_sync(*args, **kwargs):
        result = real_preview(*args, **kwargs)
        synced.append(receiver.run_now(KIND_SYNC))
        return result

    monkeypatch.setattr(billing, "compute_billing_preview", preview_with_sync)
    assert billing.release_billing_run(db, run_id, runtime=runtime).status == "released"
    assert synced[0]["downloaded"] == 1 and synced[0]["import"] == "-"
