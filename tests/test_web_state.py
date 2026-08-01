# -*- coding: utf-8 -*-
"""Tests for the admin web interface's shared state (shareomat.web.state)."""

from __future__ import annotations

import threading

from shareomat.web.state import WebState


def test_trigger_run_rejects_parallel_runs() -> None:
    hold = threading.Event()
    started = threading.Event()
    finished = threading.Event()
    calls = 0

    def callback() -> None:
        nonlocal calls
        calls += 1
        started.set()
        hold.wait()
        finished.set()

    state = WebState()
    state.register_on_run(callback)

    assert state.trigger_run() is True
    assert started.wait(timeout=5), "Callback never started"
    assert state.trigger_run() is False

    hold.set()
    assert finished.wait(timeout=5), "Callback never finished"
    assert calls == 1


def test_trigger_run_without_callback_returns_false() -> None:
    state = WebState()
    assert state.trigger_run() is False


def test_pop_flash_is_one_shot() -> None:
    state = WebState()
    state.set_flash("uploaded", ok=True)

    assert state.pop_flash() == ("uploaded", True)
    assert state.pop_flash() == ("", True)


def test_csrf_token_is_stable_per_instance() -> None:
    state = WebState()
    token = state.csrf_token
    assert token == state.csrf_token
    assert len(token) > 20


def test_warnings_are_deduplicated() -> None:
    state = WebState()
    state.add_warning("same message")
    state.add_warning("same message")
    assert state.get()["warnings"] == ["same message"]

    state.clear_warnings()
    assert state.get()["warnings"] == []
