# -*- coding: utf-8 -*-
"""
File: shareomat/live/hysteresis.py

Purpose:
    Display state "surplus / no surplus" with hysteresis and time
    stabilisation (ADR-0007, 3.5): slow towards "surplus", fast towards
    "no surplus". A wrong recommendation costs grid power, a missed one nothing.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""

from __future__ import annotations

import math
from datetime import datetime

from shareomat.live.model import DISPLAY_NO_SURPLUS, DISPLAY_SURPLUS, STATUS_COMPLETE, LiveParams


class SurplusDisplay:
    """State is kept in memory only; after a restart it starts at "no surplus"."""

    def __init__(self, params: LiveParams) -> None:
        self._params = params
        self.state = DISPLAY_NO_SURPLUS
        self._above_since: datetime | None = None
        self._below_since: datetime | None = None

    def _reset(self) -> None:
        self.state = DISPLAY_NO_SURPLUS
        self._above_since = None
        self._below_since = None

    def update(self, status: str, surplus_w: float | None, now: datetime) -> str:
        params = self._params
        if status != STATUS_COMPLETE or surplus_w is None:
            self._reset()               # a surplus never survives a data gap
            return self.state

        if self.state == DISPLAY_NO_SURPLUS:
            if surplus_w >= params.on_threshold_w:
                if self._above_since is None:
                    self._above_since = now
                if (now - self._above_since).total_seconds() >= params.on_hold_s:
                    self.state = DISPLAY_SURPLUS
                    self._above_since = None
                    self._below_since = None
            else:
                self._above_since = None
            return self.state

        if surplus_w <= 0:
            self._reset()               # deficit: immediately
        elif surplus_w < params.off_threshold_w:
            if self._below_since is None:
                self._below_since = now
            if (now - self._below_since).total_seconds() >= params.off_hold_s:
                self._reset()
        else:
            self._below_since = None
        return self.state

    def display_kw(self, surplus_w: float | None) -> float | None:
        """The number shown: rounded down to the display step, never up."""
        if self.state != DISPLAY_SURPLUS or surplus_w is None:
            return None
        step = self._params.display_step_kw
        return round(math.floor((surplus_w / 1000.0) / step + 1e-9) * step, 3)
