# -*- coding: utf-8 -*-
"""
File: shareomat/ha/entities/buttons.py

Purpose:
    Button entity definitions, published under the Shareomat Engine device.
    Add a new button by adding one line to BUTTONS.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ButtonDef:
    """One action button — pressing it publishes payload_press to its command topic."""

    uid_suffix: str
    name: str
    command_topic_suffix: str
    payload_press: str
    icon: str | None = None


BUTTONS: list[ButtonDef] = [
    ButtonDef("run_now", "Shareomat Engine Run Now", "cmd/run_once", "run", icon="mdi:play-circle"),
]
