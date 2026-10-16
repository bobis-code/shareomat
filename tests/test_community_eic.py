# -*- coding: utf-8 -*-
"""Tests for the community EIC: check-character validation, storage, form
handling, and the SDAT receiver gate (docs/sdat_leg_import.md §9–11)."""

from __future__ import annotations

import sqlite3

import pytest

from shareomat.core.pipeline.raw import sdat_ch
from shareomat.database.community import get_community, save_community
from shareomat.database.sqlite import connect, init_db
from shareomat.models.community import Community
from shareomat.web.pages import community as community_page
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state

# Real, publicly listed EICs (ENTSO-E / Swissgrid) — the check character of
# each is known-good, so they pin the algorithm rather than restate it.
REAL_EICS = [
    "10YCH-SWISSGRIDZ",
    "10X1001A1001A450",
    "10YDE-RWENET---I",
    "10YFR-RTE------C",
    "10YAT-APG------L",
]


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    return path


def _post(form) -> RequestContext:
    form = dict(form)
    form["csrf_token"] = get_state().csrf_token
    return RequestContext(method="POST", ingress_path="", form=form, query={})


# ── EIC format ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("eic", REAL_EICS)
def test_real_eics_are_valid(eic):
    assert sdat_ch.is_valid_eic(eic)


@pytest.mark.parametrize("eic", REAL_EICS)
def test_wrong_check_character_is_rejected(eic):
    wrong = "A" if eic[15] != "A" else "B"
    assert not sdat_ch.is_valid_eic(eic[:15] + wrong)


@pytest.mark.parametrize("value", ["", "10YCH-SWISSGRID", "10YCH-SWISSGRIDZZ", "10YCH_SWISSGRIDZ"])
def test_malformed_eics_are_rejected(value):
    assert not sdat_ch.is_valid_eic(value)


def test_normalize_eic_strips_and_uppercases():
    assert sdat_ch.normalize_eic("  10ych-swiss gridz ") == "10YCH-SWISSGRIDZ"


# ── Storage ───────────────────────────────────────────────────────────────────


def test_eic_roundtrip(db_path):
    save_community(db_path, Community(community_id="LEG-1", name="Test", eic_code="10YCH-SWISSGRIDZ"))
    assert get_community(db_path).eic_code == "10YCH-SWISSGRIDZ"


def test_existing_database_gets_eic_column(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE communities (id INTEGER PRIMARY KEY, community_id TEXT NOT NULL UNIQUE, "
        "name TEXT NOT NULL, address_line TEXT NOT NULL DEFAULT '', postal_code TEXT NOT NULL DEFAULT '', "
        "city TEXT NOT NULL DEFAULT '', active INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO communities (community_id, name, created_at, updated_at) VALUES ('LEG-1', 'Alt', 'x', 'x')"
    )
    conn.commit()
    conn.close()

    init_db(path)

    with connect(path) as conn:
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(communities)")}
    assert "eic_code" in columns
    assert get_community(path).eic_code == ""


# ── Form ──────────────────────────────────────────────────────────────────────


def test_form_saves_normalized_eic(db_path):
    community_page.handle_post(_post({"community_id": "LEG-1", "name": "Test", "eic_code": " 10ych-swissgridz "}))
    assert get_community(db_path).eic_code == "10YCH-SWISSGRIDZ"


def test_form_allows_empty_eic(db_path):
    community_page.handle_post(_post({"community_id": "LEG-1", "name": "Test", "eic_code": ""}))
    assert get_community(db_path).eic_code == ""


def test_form_rejects_typo_in_eic(db_path):
    with pytest.raises(FormError, match="kein gültiger EIC"):
        community_page.handle_post(_post({"community_id": "LEG-1", "name": "Test", "eic_code": "10YCH-SWISSGRIDA"}))
    assert get_community(db_path) is None


# ── Receiver gate ─────────────────────────────────────────────────────────────

OWN = "10YCH-SWISSGRIDZ"


def test_receiver_gate_accepts_own_cem_c40():
    sdat_ch.validate_receiver(OWN.lower(), "CEM", "C40", OWN)


@pytest.mark.parametrize(
    ("receiver", "role", "reason", "configured", "code"),
    [
        (OWN, "CEM", "C40", "", "EIC_NOT_CONFIGURED"),
        ("10X1001A1001A450", "CEM", "C40", OWN, "WRONG_RECEIVER"),
        (OWN, "DDM", "C40", OWN, "WRONG_ROLE"),
        (OWN, "CEM", "E66", OWN, "WRONG_BUSINESS_REASON"),
    ],
)
def test_receiver_gate_rejects(receiver, role, reason, configured, code):
    with pytest.raises(sdat_ch.SdatChValidationError) as exc:
        sdat_ch.validate_receiver(receiver, role, reason, configured)
    assert exc.value.code == code
