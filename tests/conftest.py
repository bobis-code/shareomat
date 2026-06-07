# -*- coding: utf-8 -*-
"""
File: tests/conftest.py

Purpose:
    Pytest configuration — ensures the project root is on sys.path
    so that 'self_leg' package imports work without installation.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
