# -*- coding: utf-8 -*-
"""Tests for the process-wide settlement run guard."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace

import main as app_main


def _config(tmp_path):
    return SimpleNamespace(
        paths=SimpleNamespace(
            inbox=tmp_path / "inbox",
            reports=tmp_path / "reports",
        ),
        mqtt=SimpleNamespace(),
    )


def test_run_safe_cycle_rejects_concurrent_runs(monkeypatch, tmp_path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls = 0

    def fake_run(config_path, mqtt_client=None) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        release.wait(timeout=2)

    monkeypatch.setattr(app_main, "run", fake_run)

    config = _config(tmp_path)
    first = threading.Thread(
        target=app_main._run_safe_cycle,
        args=(Path("config/leg_config.yaml"), config, None),
    )
    first.start()
    assert entered.wait(timeout=1)

    assert app_main._run_safe_cycle(Path("config/leg_config.yaml"), config, None) is False

    release.set()
    first.join(timeout=1)
    assert calls == 1


def test_run_safe_cycle_allows_next_run_after_completion(monkeypatch, tmp_path) -> None:
    calls = 0

    def fake_run(config_path, mqtt_client=None) -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(app_main, "run", fake_run)

    config = _config(tmp_path)
    assert app_main._run_safe_cycle(Path("config/leg_config.yaml"), config, None) is True
    assert app_main._run_safe_cycle(Path("config/leg_config.yaml"), config, None) is True
    assert calls == 2
