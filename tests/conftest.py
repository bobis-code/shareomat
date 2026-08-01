# -*- coding: utf-8 -*-
"""
File: tests/conftest.py

Purpose:
    Pytest configuration — ensures the project root is on sys.path
    so that 'shareomat' package imports work without installation.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


@pytest.fixture(autouse=True)
def isolate_web_state(monkeypatch):
    """Give every test a private WebState instance.

    Patches the module-level singleton so get_state() returns a fresh object.
    This prevents web UI tests from interfering with each other when run in
    any order or in parallel.
    """
    import shareomat.web.state as _ws
    from shareomat.web.state import WebState

    monkeypatch.setattr(_ws, "_state", WebState())
