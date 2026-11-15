# -*- coding: utf-8 -*-
"""SDAT-CH routing in the automatic engine (meter_data_source) and the confirmed code values.

The parser itself is tested in tests/test_sdat_ch_parser.py.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from shareomat.core.leg_runner import parse_file
from shareomat.core.pipeline.raw import sdat_ch
from shareomat.leg_const import METER_DATA_SOURCE_EMAIL_CSV, METER_DATA_SOURCE_SDAT_LEG
from shareomat.models.meter_data import ImportFile
from tests.sdat_fixtures import CONSUMER, PRODUCER, Document, build, leg_day


def test_product_id_to_energy_type_matches_spec():
    assert sdat_ch.PRODUCT_ID_TO_ENERGY_TYPE == {
        "8716867000030": "total",
        "2404050010123": "local",
        "2404050010124": "residual",
    }


def test_parse_file_routes_sdat_to_new_parser_only_when_selected(tmp_path):
    path = tmp_path / "e66.xml"
    path.write_bytes(build(Document(series=leg_day(["2.000", "1.500"], ["1.000", "1.500"], ["3.000", "1.000"], ["1.000", "1.000"]))))
    imp = ImportFile(path=path, file_type="sdat", sha256="x")

    readings = parse_file(imp, 15, None, meter_data_source=METER_DATA_SOURCE_SDAT_LEG)
    # Only the measured total becomes a reading: consumption as import, production as export.
    assert sorted((r.meter_id, r.direction, r.value_kwh) for r in readings) == [
        (CONSUMER, "import", 1.5), (CONSUMER, "import", 2.0), (PRODUCER, "export", 1.0), (PRODUCER, "export", 3.0),
    ]
    assert readings[0].slot_start == datetime(2026, 11, 30, 23, 0, tzinfo=timezone.utc)


def test_parse_file_refuses_a_broken_sdat_document_instead_of_guessing(tmp_path):
    path = tmp_path / "broken.xml"
    path.write_text('<?xml version="1.0"?><root></root>', encoding="utf-8")
    imp = ImportFile(path=path, file_type="sdat", sha256="x")
    with pytest.raises(sdat_ch.SdatChValidationError):
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
