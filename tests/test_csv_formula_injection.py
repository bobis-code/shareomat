# -*- coding: utf-8 -*-
"""S-06: CSV reports must not let names/IDs become spreadsheet formulas."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shareomat.core.report import leg_report
from shareomat.core.report.leg_report import (
    _text_cell,
    write_billing_csv,
    write_billing_json,
    write_community_audit_csv,
    write_ledger_csv,
    write_match_csv,
)
from shareomat.models.billing import BillingRecord, MatchResult

START = datetime(2026, 11, 1, tzinfo=timezone.utc)
END = datetime(2026, 12, 1, tzinfo=timezone.utc)

DANGEROUS = [
    "=HYPERLINK(\"https://evil.example\",\"Klick\")",
    "+41 61 000 00 00",
    "-2+3+cmd|' /C calc'!A0",
    "@SUM(1+1)*cmd|' /C calc'!A0",
    "\t=1+1",
    "\r=1+1",
    "\n=1+1",
    "  =1+1",
    " =1+1",
    "＝1+1",
    "＋1",
    "－1",
    "＠SUM(A1)",
]

HARMLESS = ["Haus Müller", "CH1234567890123456789012345678901", "PV 12 kWp", "1-Zimmer", "a=b", "O'Neil", ""]


@pytest.mark.parametrize("value", DANGEROUS)
def test_dangerous_text_gets_apostrophe(value):
    assert _text_cell(value) == "'" + value


@pytest.mark.parametrize("value", HARMLESS)
def test_harmless_text_is_unchanged(value):
    assert _text_cell(value) == value


def _record(participant_id: str, label: str, meter_id: str) -> BillingRecord:
    return BillingRecord(
        participant_id=participant_id, label=label, meter_ids=[meter_id],
        period_start=START, period_end=END, slot_count=2,
        total_export_kwh=0.0, local_supplied_kwh=0.0, grid_export_kwh=0.0,
        total_import_kwh=3.5, local_received_kwh=1.25, grid_import_kwh=2.25,
        local_rate_chf=0.2, grid_rate_chf=0.3,
        local_cost_chf=0.25, grid_cost_chf=0.675, total_cost_chf=0.925,
        producer_payout_chf=-0.1234,
        created_at=START,
    )


def _rows(path: Path, encoding="utf-8") -> list[list[str]]:
    with path.open(newline="", encoding=encoding) as f:
        return list(csv.reader(f))


def _no_cell_is_formula(rows):
    for row in rows:
        for cell in row:
            stripped = cell.lstrip("  ")
            if stripped.startswith(leg_report._FORMULA_TRIGGERS):
                # Only numeric cells may start with "-" (negative values).
                float(cell)


def test_billing_csv_text_fields_are_neutralised_numbers_untouched(tmp_path):
    payload = "=cmd|' /C calc'!A0"
    path = write_billing_csv([_record(payload, "@evil", "-METER")], tmp_path, START)
    header, row = _rows(path)
    values = dict(zip(header, row))
    assert values["participant_id"] == "'" + payload
    assert values["label"] == "'@evil"
    assert values["meter_ids"] == "'-METER"
    # Numeric columns stay plain numbers, including negative ones.
    assert values["total_import_kwh"] == "3.5"
    assert values["producer_payout_chf"] == "-0.1234"
    assert float(values["total_cost_chf"]) == pytest.approx(0.925)
    _no_cell_is_formula([row])


def test_billing_csv_harmless_names_unchanged(tmp_path):
    path = write_billing_csv([_record("P1", "Haus Müller", "CH123")], tmp_path, START)
    header, row = _rows(path)
    values = dict(zip(header, row))
    assert (values["participant_id"], values["label"], values["meter_ids"]) == ("P1", "Haus Müller", "CH123")


def test_billing_json_keeps_raw_values_for_machine_processing(tmp_path):
    path = write_billing_json([_record("=P1", "+label", "-M1")], tmp_path, START)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (data[0]["participant_id"], data[0]["label"], data[0]["meter_ids"]) == ("=P1", "+label", ["-M1"])


def _match_result() -> MatchResult:
    return MatchResult(
        slot_start=START,
        total_export_kwh=2.0, total_import_kwh=3.0, local_shared_kwh=2.0,
        unmatched_export_kwh=0.0, unmatched_import_kwh=1.0,
        meter_local_supplied_kwh={"=EXP": 2.0}, meter_grid_export_kwh={"=EXP": 0.0},
        meter_local_received_kwh={"@IMP": 2.0}, meter_grid_import_kwh={"@IMP": 1.0},
        flows={"=EXP": {"@IMP": 2.0}},
    )


def test_ledger_csv_labels_are_neutralised(tmp_path):
    path = write_ledger_csv([_match_result()], tmp_path, START,
                            meter_labels={"=EXP": "=HYPERLINK(\"x\")", "@IMP": "+Nachbar"})
    rows = _rows(path, encoding="utf-8-sig")
    detail = rows[1]
    assert detail[1] == "'=HYPERLINK(\"x\")"
    assert detail[2] == "'+Nachbar"
    assert detail[3] == "2.000"
    _no_cell_is_formula(rows)


def test_ledger_csv_meter_id_fallback_is_neutralised(tmp_path):
    rows = _rows(write_ledger_csv([_match_result()], tmp_path, START), encoding="utf-8-sig")
    assert rows[1][1] == "'=EXP" and rows[1][2] == "'@IMP"
    _no_cell_is_formula(rows)


def test_ledger_summary_sort_order_uses_raw_labels(tmp_path):
    result = _match_result()
    labels = {"=EXP": "-B", "@IMP": "A"}
    rows = _rows(write_ledger_csv([result], tmp_path, START, meter_labels=labels), encoding="utf-8-sig")
    summary_start = rows.index(["Zähler", "Total Export [kWh]", "Lokal verkauft [kWh]", "Netzeinspeisung [kWh]",
                                "Total Import [kWh]", "Lokal erhalten [kWh]", "Netzbezug [kWh]"])
    assert [rows[summary_start + 1][0], rows[summary_start + 2][0]] == ["'-B", "A"]  # "-B" < "A"


def test_match_and_audit_csv_contain_no_formula_cells(tmp_path):
    match_rows = _rows(write_match_csv([_match_result()], tmp_path, START))
    # Meter IDs only appear behind a fixed column-name prefix.
    assert all(not h.startswith(leg_report._FORMULA_TRIGGERS) for h in match_rows[0])
    _no_cell_is_formula(match_rows[1:])
    audit_rows = _rows(write_community_audit_csv([_record("=P", "=L", "=M")], tmp_path, START))
    _no_cell_is_formula(audit_rows[1:])
