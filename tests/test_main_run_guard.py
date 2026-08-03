# -*- coding: utf-8 -*-
"""Tests for the process-wide settlement run guard in main.py."""

from __future__ import annotations

import threading
from pathlib import Path

import main as app_main
from shareomat.config import PathConfig, RuntimeConfig


def _runtime(tmp_path) -> RuntimeConfig:
    return RuntimeConfig(paths=PathConfig(
        inbox=tmp_path / "inbox", archive=tmp_path / "archive",
        reports=tmp_path / "reports", state=tmp_path / "state",
    ))


def _patch_build_and_validate(monkeypatch) -> None:
    """_run_safe_cycle builds+validates a LegConfig before calling run(); stub both."""
    monkeypatch.setattr(app_main, "build_leg_config", lambda db_path, runtime: object())
    monkeypatch.setattr(app_main, "validate_leg_config", lambda config: None)


def test_run_safe_cycle_rejects_concurrent_runs(monkeypatch, tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_run(config, mqtt_client=None, db_path=None) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(timeout=2)

    _patch_build_and_validate(monkeypatch)
    monkeypatch.setattr(app_main, "run", fake_run)

    runtime = _runtime(tmp_path)
    db_path = tmp_path / "shareomat.db"
    first = threading.Thread(
        target=app_main._run_safe_cycle,
        args=(runtime, db_path, None),
    )
    first.start()
    assert entered.wait(timeout=1)

    assert app_main._run_safe_cycle(runtime, db_path, None) is False

    release.set()
    first.join(timeout=1)
    assert calls == 1


def test_run_safe_cycle_allows_next_run_after_completion(monkeypatch, tmp_path) -> None:
    calls = 0

    def fake_run(config, mqtt_client=None, db_path=None) -> None:
        nonlocal calls
        calls += 1

    _patch_build_and_validate(monkeypatch)
    monkeypatch.setattr(app_main, "run", fake_run)

    runtime = _runtime(tmp_path)
    db_path = tmp_path / "shareomat.db"
    assert app_main._run_safe_cycle(runtime, db_path, None) is True
    assert app_main._run_safe_cycle(runtime, db_path, None) is True
    assert calls == 2


def test_run_safe_cycle_releases_lock_on_incomplete_config(monkeypatch, tmp_path) -> None:
    """A fresh/incomplete database must not permanently hold the run lock (regression test)."""
    from shareomat.database.config_builder import IncompleteConfigError

    def fake_build(db_path, runtime):
        raise IncompleteConfigError("Keine Gemeinschaft eingerichtet.")

    monkeypatch.setattr(app_main, "build_leg_config", fake_build)
    monkeypatch.setattr(app_main, "run", lambda config, mqtt_client=None: (_ for _ in ()).throw(
        AssertionError("run() must not be called when config is incomplete")
    ))

    runtime = _runtime(tmp_path)
    db_path = tmp_path / "shareomat.db"

    assert app_main._run_safe_cycle(runtime, db_path, None) is False
    # If the lock leaked on the incomplete-config path, this second call would
    # report "already running" (False) even though nothing is actually running.
    assert app_main._RUN_LOCK.acquire(blocking=False) is True
    app_main._RUN_LOCK.release()
