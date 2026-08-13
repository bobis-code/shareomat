# -*- coding: utf-8 -*-
"""Tests for the SDAT-CH-2025 parser scaffold and its meter_data_source routing.

Mirrors tests/test_external_data_placeholders.py: confirms the not-yet-
implemented parser fails clearly instead of guessing XML structure, and
that shareomat.core.leg_runner.parse_file only reaches it when a
community has explicitly switched meter_data_source to "sdat_leg".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shareomat.core.leg_runner import parse_file
from shareomat.core.pipeline.raw import sdat_ch
from shareomat.leg_const import METER_DATA_SOURCE_EMAIL_CSV, METER_DATA_SOURCE_SDAT_LEG
from shareomat.models.meter_data import ImportFile


def test_parse_sdat_ch_raises_pending_verification():
    with pytest.raises(sdat_ch.SdatChNotImplementedError, match="SDAT-CH-2025"):
        sdat_ch.parse_sdat_ch(Path("does-not-need-to-exist.xml"))


def test_product_id_to_energy_type_matches_spec():
    assert sdat_ch.PRODUCT_ID_TO_ENERGY_TYPE == {
        "8716867000030": "total",
        "2404050010123": "local",
        "2404050010124": "residual",
    }


def test_parse_file_routes_sdat_to_new_parser_only_when_selected(tmp_path):
    imp = ImportFile(path=tmp_path / "not-yet-fetched.xml", file_type="sdat", sha256="x")

    with pytest.raises(sdat_ch.SdatChNotImplementedError):
        parse_file(imp, 15, None, meter_data_source=METER_DATA_SOURCE_SDAT_LEG)


def test_parse_file_keeps_legacy_experimental_parser_by_default(tmp_path):
    # A well-formed but empty document: the legacy parser tolerates it (no
    # MeteringPoint elements → empty result) instead of raising, unlike the
    # real SDAT-CH parser above.
    path = tmp_path / "legacy.xml"
    path.write_text('<?xml version="1.0"?><root></root>', encoding="utf-8")
    imp = ImportFile(path=path, file_type="sdat", sha256="x")

    readings = parse_file(imp, 15, None, meter_data_source=METER_DATA_SOURCE_EMAIL_CSV)
    assert readings == []
