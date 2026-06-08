# -*- coding: utf-8 -*-
"""
File: tests/test_pipeline_integration.py

Purpose:
    End-to-end integration test for the complete SELF LEG settlement pipeline.
    Runs the full cycle — inbox → parse → match → bill → report → archive —
    with synthetic CSV meter data covering all sharing scenarios.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    Test data covers four 15-minute slots:
        slot 1  12:00  Überschuss   — production > consumption (solar covers all + feeds grid)
        slot 2  12:15  Mangel       — production < consumption (proportional split + grid draw)
        slot 3  12:30  Ausgeglichen — production == consumption (no grid interaction)
        slot 4  12:45  Keine Prod.  — zero production (all consumption from grid)

    Expected billing totals (manually verified):
        solar  participant: supplied=1.3 kWh  grid_export=0.5 kWh  cost=0.00 CHF
        cons_a participant: received=0.8 kWh  grid_import=0.6 kWh  cost=0.23 CHF
        cons_b participant: received=0.5 kWh  grid_import=0.4 kWh  cost=0.15 CHF

    Community audit invariants:
        Σ local_supplied = Σ local_received = 1.3 kWh
        balance_export = balance_import = settlement_balance = 0.0
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import types
from pathlib import Path

import pytest
import yaml

from self_leg.core.leg_runner import run

# ── Meter identifiers ─────────────────────────────────────────────────────────

METER_PROD = "CH_METER_PROD_001"
METER_A    = "CH_METER_CONS_A"
METER_B    = "CH_METER_CONS_B"

_LOCAL_RATE = 0.10
_GRID_RATE  = 0.25

# ── Test CSV rows ─────────────────────────────────────────────────────────────
# (timestamp, meter_id, value_kwh, direction)

_CSV_ROWS = [
    # slot 1  12:00  Überschuss: prod=1.0, A=0.3, B=0.2 → local_shared=0.5, feed_in=0.5
    ("2024-06-01T12:00:00+00:00", METER_PROD, 1.0, "export"),
    ("2024-06-01T12:00:00+00:00", METER_A,    0.3, "import"),
    ("2024-06-01T12:00:00+00:00", METER_B,    0.2, "import"),
    # slot 2  12:15  Mangel: prod=0.3, A=0.4, B=0.2 → local_shared=0.3, grid_draw=0.3
    ("2024-06-01T12:15:00+00:00", METER_PROD, 0.3, "export"),
    ("2024-06-01T12:15:00+00:00", METER_A,    0.4, "import"),
    ("2024-06-01T12:15:00+00:00", METER_B,    0.2, "import"),
    # slot 3  12:30  Ausgeglichen: prod=0.5, A=0.3, B=0.2 → local_shared=0.5, no grid
    ("2024-06-01T12:30:00+00:00", METER_PROD, 0.5, "export"),
    ("2024-06-01T12:30:00+00:00", METER_A,    0.3, "import"),
    ("2024-06-01T12:30:00+00:00", METER_B,    0.2, "import"),
    # slot 4  12:45  Keine Produktion: A=0.4, B=0.3 → all from grid
    ("2024-06-01T12:45:00+00:00", METER_A,    0.4, "import"),
    ("2024-06-01T12:45:00+00:00", METER_B,    0.3, "import"),
]

# ── Expected billing values ───────────────────────────────────────────────────
#
# solar:  local_supplied = 0.5 + 0.3 + 0.5 + 0.0 = 1.3 kWh
#         grid_export    = 0.5 + 0.0 + 0.0 + 0.0 = 0.5 kWh
#         local_received = 0  grid_import = 0  cost = 0
#
# cons_a: local_received = 0.3 + 0.2 + 0.3 + 0.0 = 0.8 kWh
#         grid_import    = 0.0 + 0.2 + 0.0 + 0.4 = 0.6 kWh
#
# cons_b: local_received = 0.2 + 0.1 + 0.2 + 0.0 = 0.5 kWh
#         grid_import    = 0.0 + 0.1 + 0.0 + 0.3 = 0.4 kWh

_EXPECTED = {
    "solar": {
        "total_export_kwh":   1.8,
        "local_supplied_kwh": 1.3,
        "grid_export_kwh":    0.5,
        "total_import_kwh":   0.0,
        "local_received_kwh": 0.0,
        "grid_import_kwh":    0.0,
        "total_cost_chf":     0.0,
    },
    "cons_a": {
        "total_export_kwh":   0.0,
        "local_supplied_kwh": 0.0,
        "grid_export_kwh":    0.0,
        "total_import_kwh":   1.4,
        "local_received_kwh": 0.8,
        "grid_import_kwh":    0.6,
        "local_cost_chf":     0.08,
        "grid_cost_chf":      0.15,
        "total_cost_chf":     0.23,
    },
    "cons_b": {
        "total_export_kwh":   0.0,
        "local_supplied_kwh": 0.0,
        "grid_export_kwh":    0.0,
        "total_import_kwh":   0.9,
        "local_received_kwh": 0.5,
        "grid_import_kwh":    0.4,
        "local_cost_chf":     0.05,
        "grid_cost_chf":      0.10,
        "total_cost_chf":     0.15,
    },
}


# ── Setup helpers ─────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    """Compute SHA-256 hex digest of a file."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_env(root: Path) -> types.SimpleNamespace:
    """Create temp directories, test CSV, and config YAML for one pipeline run."""
    inbox   = root / "inbox"
    archive = root / "archive"
    reports = root / "reports"
    state   = root / "state"
    for d in (inbox, archive, reports, state):
        d.mkdir()

    csv_path = inbox / "readings_20240601.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp", "meter_id", "value_kwh", "direction"])
        for row in _CSV_ROWS:
            writer.writerow(row)

    config = {
        "leg": {"community_id": "TEST-ZEV-001", "name": "Test Community"},
        "participants": [
            {"participant_id": "solar",  "label": "Solar PV",   "participant_type": "producer", "active": True},
            {"participant_id": "cons_a", "label": "Consumer A", "participant_type": "consumer", "active": True},
            {"participant_id": "cons_b", "label": "Consumer B", "participant_type": "consumer", "active": True},
        ],
        "meters": [
            {"meter_id": METER_PROD, "participant_id": "solar",  "label": "PV Meter", "role": "producer", "active": True},
            {"meter_id": METER_A,    "participant_id": "cons_a", "label": "Meter A",  "role": "consumer", "active": True},
            {"meter_id": METER_B,    "participant_id": "cons_b", "label": "Meter B",  "role": "consumer", "active": True},
        ],
        "tariffs": {
            "local_rate_chf_kwh": _LOCAL_RATE,
            "grid_rate_chf_kwh":  _GRID_RATE,
            "feed_in_rate_chf_kwh": 0.05,
        },
        "paths": {
            "inbox":   str(inbox),
            "archive": str(archive),
            "reports": str(reports),
            "state":   str(state),
        },
        "processing": {
            "slot_minutes":         15,
            "archive_processed":    True,
            "unknown_meter_policy": "fail",
        },
    }
    config_path = root / "leg_config.yaml"
    with config_path.open("w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True)

    return types.SimpleNamespace(
        config_path=config_path,
        inbox=inbox,
        archive=archive,
        reports=reports,
        state=state,
        csv_path=csv_path,
        sha256=_sha256(csv_path),
    )


# ── Class-scoped fixture ──────────────────────────────────────────────────────


@pytest.fixture(scope="class")
def after_first_run(tmp_path_factory):
    """Run the full pipeline once; all tests in the class share the resulting state."""
    env = _make_env(tmp_path_factory.mktemp("integration"))
    run(env.config_path)
    return env


# ── First-run assertions ──────────────────────────────────────────────────────


class TestFirstRun:

    def test_report_files_created(self, after_first_run):
        """Pipeline creates exactly six reports: billing CSV/JSON, match detail, audit, summary, energy ledger."""
        files = list(after_first_run.reports.iterdir())
        assert len(files) == 6

    def test_billing_csv_contains_all_three_participants(self, after_first_run):
        """Billing CSV has one row per participant including the supplier."""
        billing_csv = next(after_first_run.reports.glob("billing_*.csv"))
        with billing_csv.open(encoding="utf-8") as f:
            pids = {row["participant_id"] for row in csv.DictReader(f)}
        assert pids == {"solar", "cons_a", "cons_b"}

    def test_billing_csv_energy_and_cost_values(self, after_first_run):
        """Billing CSV energy and cost values match the manually computed expected totals."""
        billing_csv = next(after_first_run.reports.glob("billing_*.csv"))
        with billing_csv.open(encoding="utf-8") as f:
            rows = {r["participant_id"]: r for r in csv.DictReader(f)}

        for pid, expected in _EXPECTED.items():
            for field, value in expected.items():
                actual = float(rows[pid][field])
                assert actual == pytest.approx(value, abs=1e-6), \
                    f"{pid}/{field}: expected {value}, got {actual}"

    def test_billing_csv_meter_ids_map_to_correct_participant(self, after_first_run):
        """Billing CSV meter_ids column references the correct meter for each participant."""
        billing_csv = next(after_first_run.reports.glob("billing_*.csv"))
        with billing_csv.open(encoding="utf-8") as f:
            rows = {r["participant_id"]: r for r in csv.DictReader(f)}
        assert METER_PROD in rows["solar"]["meter_ids"]
        assert METER_A in rows["cons_a"]["meter_ids"]
        assert METER_B in rows["cons_b"]["meter_ids"]

    def test_billing_json_required_fields_present(self, after_first_run):
        """Billing JSON contains all required fields."""
        billing_json = next(after_first_run.reports.glob("billing_*.json"))
        data = json.loads(billing_json.read_text(encoding="utf-8"))

        assert len(data) == 3
        required = {
            "participant_id", "label", "meter_ids", "period_start", "period_end",
            "slot_count", "source_files",
            "total_export_kwh", "local_supplied_kwh", "grid_export_kwh",
            "total_import_kwh", "local_received_kwh", "grid_import_kwh",
            "local_cost_chf", "grid_cost_chf", "total_cost_chf",
        }
        for record in data:
            assert required <= set(record), f"missing fields in: {set(record)}"

    def test_billing_json_slot_count_and_source_files(self, after_first_run):
        """JSON slot_count equals 4 and source_files lists the input CSV filename."""
        billing_json = next(after_first_run.reports.glob("billing_*.json"))
        data = json.loads(billing_json.read_text(encoding="utf-8"))
        for record in data:
            assert record["slot_count"] == 4
            assert "readings_20240601.csv" in record["source_files"]

    def test_match_detail_has_four_rows(self, after_first_run):
        """Match detail CSV has exactly one row per 15-minute slot."""
        match_csv = next(after_first_run.reports.glob("match_detail_*.csv"))
        with match_csv.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 4

    def test_match_detail_has_consumer_and_supplier_columns(self, after_first_run):
        """Match detail CSV has local_received and grid_import for consumers, local_supplied and grid_export for suppliers."""
        match_csv = next(after_first_run.reports.glob("match_detail_*.csv"))
        with match_csv.open(encoding="utf-8") as f:
            fieldnames = csv.DictReader(f).fieldnames
        assert f"local_received_{METER_A}" in fieldnames
        assert f"grid_import_{METER_A}" in fieldnames
        assert f"local_supplied_{METER_PROD}" in fieldnames
        assert f"grid_export_{METER_PROD}" in fieldnames

    def test_match_detail_slot1_excess_all_local_no_grid(self, after_first_run):
        """Slot 1 (excess): consumers receive full import locally; grid draw is zero."""
        match_csv = next(after_first_run.reports.glob("match_detail_*.csv"))
        with match_csv.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        slot1 = rows[0]
        assert float(slot1[f"local_received_{METER_A}"]) == pytest.approx(0.3, abs=1e-4)
        assert float(slot1[f"grid_import_{METER_A}"])    == pytest.approx(0.0, abs=1e-4)
        assert float(slot1[f"local_supplied_{METER_PROD}"]) == pytest.approx(0.5, abs=1e-4)
        assert float(slot1[f"grid_export_{METER_PROD}"])    == pytest.approx(0.5, abs=1e-4)

    def test_match_detail_slot4_no_production_all_grid(self, after_first_run):
        """Slot 4 (no production): local share is zero; all consumption drawn from grid."""
        match_csv = next(after_first_run.reports.glob("match_detail_*.csv"))
        with match_csv.open(encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        slot4 = rows[3]
        assert float(slot4[f"local_received_{METER_A}"]) == pytest.approx(0.0, abs=1e-4)
        assert float(slot4[f"grid_import_{METER_A}"])    == pytest.approx(0.4, abs=1e-4)

    def test_community_audit_all_balances_are_zero(self, after_first_run):
        """Community audit CSV shows balance_export, balance_import, settlement_balance all zero."""
        audit_csv = next(after_first_run.reports.glob("community_audit_*.csv"))
        with audit_csv.open(encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        assert float(row["balance_export_kwh"])    == pytest.approx(0.0, abs=1e-4)
        assert float(row["balance_import_kwh"])    == pytest.approx(0.0, abs=1e-4)
        assert float(row["settlement_balance_kwh"]) == pytest.approx(0.0, abs=1e-4)

    def test_community_audit_supplied_equals_received(self, after_first_run):
        """Community audit: sum_local_supplied == sum_local_received."""
        audit_csv = next(after_first_run.reports.glob("community_audit_*.csv"))
        with audit_csv.open(encoding="utf-8") as f:
            row = next(csv.DictReader(f))
        supplied = float(row["sum_local_supplied_kwh"])
        received = float(row["sum_local_received_kwh"])
        assert supplied == pytest.approx(received, abs=1e-6)
        assert supplied == pytest.approx(1.3, abs=1e-4)

    def test_community_summary_json_structure(self, after_first_run):
        """Community summary JSON contains required KPI fields and correct values."""
        summary_json = next(after_first_run.reports.glob("community_summary_*.json"))
        data = json.loads(summary_json.read_text(encoding="utf-8"))

        assert data["community_id"] == "TEST-ZEV-001"
        assert data["slot_count"] == 4
        assert data["settlement_balance_ok"] is True
        assert float(data["total_export_kwh"])  == pytest.approx(1.8, abs=1e-4)
        assert float(data["total_import_kwh"])  == pytest.approx(2.3, abs=1e-4)
        assert float(data["local_shared_kwh"])  == pytest.approx(1.3, abs=1e-4)
        assert float(data["grid_export_kwh"])   == pytest.approx(0.5, abs=1e-4)
        assert float(data["grid_import_kwh"])   == pytest.approx(1.0, abs=1e-4)
        for field in ("self_consumption_ratio_pct", "autarky_ratio_pct"):
            assert field in data

    def test_state_file_records_sha256_and_report_filenames(self, after_first_run):
        """State file persists the processed file's SHA-256 and all six report filenames."""
        env = after_first_run
        state_file = env.state / "processed_files.json"
        assert state_file.exists()

        data = json.loads(state_file.read_text(encoding="utf-8"))
        entry = data["entries"][0]
        assert entry["sha256"] == env.sha256
        assert entry["filename"] == "readings_20240601.csv"
        assert len(entry["report_files"]) == 6

    def test_source_csv_moved_to_archive(self, after_first_run):
        """Source CSV is removed from inbox and placed in archive after reports are written."""
        env = after_first_run
        assert not env.csv_path.exists()
        archived = list(env.archive.glob("readings_20240601*.csv"))
        assert len(archived) == 1


# ── Idempotency ───────────────────────────────────────────────────────────────


def test_second_run_with_same_file_produces_no_new_reports(tmp_path):
    """Re-delivering an identical file (same SHA-256) produces no additional reports."""
    env = _make_env(tmp_path)

    run(env.config_path)
    reports_after_first = {p.name for p in env.reports.iterdir()}
    assert len(reports_after_first) == 6

    archived = next(env.archive.iterdir())
    shutil.copy(archived, env.inbox / archived.name)

    run(env.config_path)
    reports_after_second = {p.name for p in env.reports.iterdir()}

    assert reports_after_first == reports_after_second
