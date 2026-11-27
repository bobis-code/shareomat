# -*- coding: utf-8 -*-
"""
File: tests/test_official_billing_scenarios.py

Purpose:
    The four sharing scenarios of the former CSV pipeline test
    (tests/test_pipeline_integration.py, removed with the CSV path in P2c),
    now on the grid operator's official split: surplus, shortage, balanced
    and no production, with the same manually verified totals.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Four quarter hours on 01.06.2024 (UTC):
        12:00  surplus    production 1.0 > consumption 0.5 → shared 0.5, 0.5 to the grid
        12:15  shortage   production 0.3 < consumption 0.6 → shared 0.3 pro rata, 0.3 from the grid
        12:30  balanced   production 0.5 = consumption 0.5 → no grid
        12:45  no prod.   production 0                     → everything from the grid

    The split is what the grid operator delivers (tests/official_delivery.py,
    Art. 19g StromVV); Shareomat bills it as it is.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.billing import compute_billing_preview
from shareomat.database.community import save_community
from shareomat.database.meters import create_meter
from shareomat.database.participants import create_participant
from shareomat.database.sqlite import init_db
from shareomat.database.tariffs import create_tariff
from shareomat.models.community import Community
from shareomat.models.meter import Meter
from shareomat.models.participant import Participant
from shareomat.models.tariff import Tariff
from tests.official_delivery import deliver, mpid, split, use_official_data

PROD, A, B = mpid(1), mpid(2), mpid(3)
DAY = date(2024, 6, 1)

_ROWS = [
    ("2024-06-01T12:00:00+00:00", PROD, "1.0", "export"),
    ("2024-06-01T12:00:00+00:00", A, "0.3", "import"),
    ("2024-06-01T12:00:00+00:00", B, "0.2", "import"),
    ("2024-06-01T12:15:00+00:00", PROD, "0.3", "export"),
    ("2024-06-01T12:15:00+00:00", A, "0.4", "import"),
    ("2024-06-01T12:15:00+00:00", B, "0.2", "import"),
    ("2024-06-01T12:30:00+00:00", PROD, "0.5", "export"),
    ("2024-06-01T12:30:00+00:00", A, "0.3", "import"),
    ("2024-06-01T12:30:00+00:00", B, "0.2", "import"),
    ("2024-06-01T12:45:00+00:00", PROD, "0", "export"),
    ("2024-06-01T12:45:00+00:00", A, "0.4", "import"),
    ("2024-06-01T12:45:00+00:00", B, "0.3", "import"),
]

# local rate 0.10, grid rate 0.25 CHF/kWh
_EXPECTED = {
    "solar": dict(local_supplied_kwh="1.3", grid_export_kwh="0.5", local_received_kwh="0", grid_import_kwh="0",
                  local_amount_chf="0", grid_amount_chf="0"),
    "cons_a": dict(local_supplied_kwh="0", grid_export_kwh="0", local_received_kwh="0.8", grid_import_kwh="0.6",
                   local_amount_chf="0.08", grid_amount_chf="0.15"),
    "cons_b": dict(local_supplied_kwh="0", grid_export_kwh="0", local_received_kwh="0.5", grid_import_kwh="0.4",
                   local_amount_chf="0.05", grid_amount_chf="0.10"),
}


@pytest.fixture(scope="module")
def preview(tmp_path_factory):
    root = tmp_path_factory.mktemp("scenarios")
    paths = {name: root / name for name in ("inbox", "archive", "reports", "state")}
    for path in paths.values():
        path.mkdir()
    db = root / "shareomat.db"
    init_db(db)
    save_community(db, Community(community_id="TEST-ZEV-001", name="Test Community"))
    use_official_data(db)
    for pid, name, meter, role in (("solar", "Solar PV", PROD, "producer"), ("cons_a", "Consumer A", A, "consumer"),
                                   ("cons_b", "Consumer B", B, "consumer")):
        create_participant(db, Participant(pid, name))
        create_meter(db, Meter(meter, pid, name, role))
    create_tariff(db, Tariff(local_rate_chf_kwh=Decimal("0.10"), grid_rate_chf_kwh=Decimal("0.25"),
                             feed_in_rate_chf_kwh=Decimal("0.05"), valid_from=date(2020, 1, 1)))
    deliver(db, root, _ROWS)
    return compute_billing_preview(db, RuntimeConfig(paths=PathConfig(**paths)), DAY, DAY)


def test_the_grid_operator_split_of_each_scenario():
    """The delivered split itself, quarter hour by quarter hour (what the grid operator computes)."""
    shares = {(meter[-1], slot.strftime("%H:%M")): local for (meter, _, slot), (_, local) in split(_ROWS).items()}
    assert shares[("1", "12:00")] == Decimal("0.500")                                        # surplus
    assert (shares[("2", "12:15")], shares[("3", "12:15")]) == (Decimal("0.200"), Decimal("0.100"))  # pro rata
    assert shares[("1", "12:30")] == Decimal("0.500")                                        # balanced
    assert (shares[("2", "12:45")], shares[("3", "12:45")]) == (Decimal(0), Decimal(0))      # no production


@pytest.mark.parametrize("participant", sorted(_EXPECTED))
def test_billing_totals_per_participant(preview, participant):
    item = next(i for i in preview.line_items if i.participant_id == participant)
    for field, expected in _EXPECTED[participant].items():
        assert getattr(item, field) == Decimal(expected), field


def test_every_kwh_received_was_supplied(preview):
    received = sum(i.local_received_kwh for i in preview.line_items)
    supplied = sum(i.local_supplied_kwh for i in preview.line_items)
    assert received == supplied == Decimal("1.3")
    assert preview.total_leg_amount_chf == Decimal("0.13")


def test_meters_map_to_their_participant(preview):
    assert {i.participant_id: tuple(i.meter_ids) for i in preview.line_items} == {
        "solar": (PROD,), "cons_a": (A,), "cons_b": (B,)}
