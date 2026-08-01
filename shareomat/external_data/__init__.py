# -*- coding: utf-8 -*-
"""
File: shareomat/external_data/__init__.py

Purpose:
    Clients for external data sources used to cross-check and forecast
    tariffs: ElCom (Swiss electricity tariff registry), the grid
    operator (EBL), BFE (official reference prices), and market forecast
    inputs (ENTSO-E day-ahead prices/generation, SNB exchange rates).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Shareomat always bills participants using the internal LEG tariff
    (shareomat.database.tariffs). Everything in this package is
    informational / for comparison and control invoices only — never a
    second, competing billing source.
"""
