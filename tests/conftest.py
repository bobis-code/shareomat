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
def isolate_ingress_state(monkeypatch):
    """Give every test a private IngressState instance.

    Patches the module-level singleton so get_state() returns a fresh object.
    This prevents HA web tests from interfering with each other when run in
    any order or in parallel.
    """
    import shareomat.ha.web_state as _ws
    from shareomat.ha.web_state import IngressState

    monkeypatch.setattr(_ws, "_state", IngressState())
