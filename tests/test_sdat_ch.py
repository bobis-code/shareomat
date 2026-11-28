# -*- coding: utf-8 -*-
"""SDAT-CH: the confirmed code values, and a broken document is refused instead of guessed.

The parser itself is tested in tests/test_sdat_ch_parser.py. The routing of
inbox files to this parser went with the CSV path in 0.6.34 (ADR-0012): SDAT
documents arrive only through the Swisseldex receiver.
"""

from __future__ import annotations

import pytest

from shareomat.core.pipeline.raw import sdat_ch


def test_product_id_to_energy_type_matches_spec():
    assert sdat_ch.PRODUCT_ID_TO_ENERGY_TYPE == {
        "8716867000030": "total",
        "2404050010123": "local",
        "2404050010124": "residual",
    }


def test_a_broken_sdat_document_is_refused_instead_of_guessed():
    with pytest.raises(sdat_ch.SdatChValidationError):
        sdat_ch.parse_sdat_ch_document(b'<?xml version="1.0"?><root></root>')
