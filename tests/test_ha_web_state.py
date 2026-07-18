# -*- coding: utf-8 -*-
"""Tests for the Ingress dashboard shared state."""

from __future__ import annotations

import threading
import time

from shareomat.ha.web_state import IngressState


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

    state = IngressState()
    state.register_on_run(callback)

    assert state.trigger_run() is True
    assert started.wait(timeout=5), "Callback never started"
    assert state.trigger_run() is False

    hold.set()
    assert finished.wait(timeout=5), "Callback never finished"
    assert calls == 1


def test_trigger_run_without_callback_returns_false() -> None:
    state = IngressState()
    assert state.trigger_run() is False


def test_pop_upload_result_is_one_shot() -> None:
    state = IngressState()
    state.set_upload_result("uploaded", ok=True)

    assert state.pop_upload_result() == ("uploaded", True)
    assert state.pop_upload_result() == ("", True)


def test_registered_meters_are_returned_as_copy() -> None:
    state = IngressState()
    state.register_meters([("CH1", "House 1")])

    meters = state.meter_list
    meters.append(("CH2", "House 2"))

    assert state.meter_list == [("CH1", "House 1")]
