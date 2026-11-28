# -*- coding: utf-8 -*-
"""Tests for the admin web interface's shared state (shareomat.web.state)."""

from __future__ import annotations

from shareomat.web.state import WebState


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
