# -*- coding: utf-8 -*-
"""
File: shareomat/external_data/ebl.py

Purpose:
    Structural placeholder for importing EBL's supplier tariffs (grid
    usage, energy, metering, feed-in) as machine-readable data.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    BLOCKED: access to EBL's digital LEG-Service (which provides this
    data) is pending EBL's approval of the user's registration, as of
    2026-08-01. No EBL endpoint or data format is known yet — this
    module intentionally raises rather than guessing a URL or schema.
    Once access is granted, implement against the confirmed format and
    store results via shareomat.database.supplier_tariffs.
"""

from __future__ import annotations


class EblNotImplementedError(NotImplementedError):
    """Raised by every function in this module until EBL grants LEG-Service platform access."""

    def __init__(self, function_name: str) -> None:
        super().__init__(
            f"{function_name}() ist noch nicht implementiert. Der Zugang zur digitalen "
            "LEG-Service-Plattform von EBL steht noch aus (Registrierung eingereicht, "
            "Freischaltung durch EBL ausstehend)."
        )


def download_ebl_tariffs(*_args, **_kwargs) -> None:
    """Download EBL's current tariff sheet. Not yet implemented — see module docstring."""
    raise EblNotImplementedError("download_ebl_tariffs")


def import_ebl_tariffs(*_args, **_kwargs) -> None:
    """Parse and store a downloaded EBL tariff file. Not yet implemented."""
    raise EblNotImplementedError("import_ebl_tariffs")


def get_ebl_grid_tariff(*_args, **_kwargs) -> None:
    """Return the current EBL grid usage tariff. Not yet implemented."""
    raise EblNotImplementedError("get_ebl_grid_tariff")


def get_ebl_energy_tariff(*_args, **_kwargs) -> None:
    """Return the current EBL residual-energy tariff. Not yet implemented."""
    raise EblNotImplementedError("get_ebl_energy_tariff")


def get_ebl_feed_in_tariff(*_args, **_kwargs) -> None:
    """Return the current EBL feed-in tariff. Not yet implemented."""
    raise EblNotImplementedError("get_ebl_feed_in_tariff")


def get_ebl_metering_cost(*_args, **_kwargs) -> None:
    """Return the current EBL metering cost. Not yet implemented."""
    raise EblNotImplementedError("get_ebl_metering_cost")
