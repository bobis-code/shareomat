# -*- coding: utf-8 -*-
"""
File: shareomat/core/raw/__init__.py

Purpose:
    Provider-specific raw parser package.
    Each module in this package handles one grid operator's file format
    and exposes a single public function:

        parse(path, slot_minutes, known_meter_ids) -> list[IntervalReading]

    Provider-specific details (column positions, header structure, timezone
    conventions, direction label mapping) stay inside this package.
    The canonical model starts at IntervalReading.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""
