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


@pytest.fixture(autouse=True)
def isolate_fixed_secrets(monkeypatch):
    """No test sees a secret another test registered (shareomat.secret_store.register_fixed_secrets);
    SHAREOMAT_* secret variables of the developer's shell never reach a test."""
    import shareomat.secret_store as store
    from shareomat.config import SECRET_FIELDS

    for variable in (*SECRET_FIELDS.values(), "SHAREOMAT_SECRETS_PATH", "SHAREOMAT_SECRET_STORE_PATH"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setattr(store, "_fixed", {})
