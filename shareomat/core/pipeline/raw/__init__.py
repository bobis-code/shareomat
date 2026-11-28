# -*- coding: utf-8 -*-
"""
File: shareomat/core/pipeline/raw/__init__.py

Purpose:
    Raw format parsers. Each module handles exactly one external format and
    normalizes it to the internal model; the billing logic never knows the
    delivery format. Today: SDAT-CH (sdat_ch.py). The EBL Excel parser was
    removed with the CSV path in 0.6.34 (ADR-0012); a later import adapter
    gets its own module here (ADR-0002, ADR-0003).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine
"""
