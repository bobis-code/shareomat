# -*- coding: utf-8 -*-
"""Tests confirming the EBL placeholder module fails clearly instead of guessing data.

BFE used to be a placeholder here too; it is now a real implementation —
see tests/test_external_data_bfe.py.
"""

from __future__ import annotations

import pytest

from shareomat.external_data import ebl


def test_ebl_functions_raise_pending_access():
    with pytest.raises(ebl.EblNotImplementedError, match="LEG-Service"):
        ebl.get_ebl_energy_tariff()
    with pytest.raises(ebl.EblNotImplementedError):
        ebl.download_ebl_tariffs()
    with pytest.raises(ebl.EblNotImplementedError):
        ebl.import_ebl_tariffs()
    with pytest.raises(ebl.EblNotImplementedError):
        ebl.get_ebl_grid_tariff()
    with pytest.raises(ebl.EblNotImplementedError):
        ebl.get_ebl_feed_in_tariff()
    with pytest.raises(ebl.EblNotImplementedError):
        ebl.get_ebl_metering_cost()
