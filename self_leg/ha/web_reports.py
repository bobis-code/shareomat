# -*- coding: utf-8 -*-
"""
Report discovery and HTML rendering helpers for the Ingress dashboard.
"""

from __future__ import annotations

import json
from datetime import datetime
from html import escape as _html_escape
from pathlib import Path

from self_leg.ha.web_state import get_state


def report_path(filename: str) -> Path | None:
    """Return a safe path inside the reports directory, or None if invalid."""
    reports = get_state().reports_path

    # Accept only a basename so query strings cannot escape the reports directory.
    safe_name = Path(filename).name
    if not safe_name or reports is None:
        return None
    path = reports / safe_name
    if not path.exists() or not path.is_file():
        return None
    return path


def list_billing_reports() -> list[tuple[str, str]]:
    """Return (stem, display_label) for each billing JSON report, newest first."""
    path = get_state().reports_path
    if path is None or not path.exists():
        return []

    # Billing JSON files are the index for a settlement run; sibling files share the date suffix.
    stems = sorted(
        [
            f.stem
            for f in path.iterdir()
            if f.name.startswith("billing_") and f.name.endswith(".json")
        ],
        reverse=True,
    )
    result: list[tuple[str, str]] = []
    for stem in stems:
        date_part = stem[len("billing_"):]
        if not date_part:
            continue
        try:
            dt = datetime.strptime(date_part, "%Y%m%d_%H%M%S")
            label = dt.strftime("%Y-%m-%d  %H:%M")
        except ValueError:
            label = date_part
        result.append((stem, label))
    return result


def _render_report_section(selected_stem: str, ingress_path: str) -> str:
    """Return HTML with billing table and community KPIs for a report stem."""
    path = get_state().reports_path
    if path is None:
        return ""

    billing_path = path / f"{selected_stem}.json"
    date_suffix = selected_stem[len("billing_"):]
    summary_path = path / f"community_summary_{date_suffix}.json"

    # If the selected billing file cannot be read, keep the dashboard alive and show an error box.
    try:
        with billing_path.open(encoding="utf-8") as f:
            records: list[dict] = json.load(f)
    except Exception:
        return '<div class="error-box" style="margin-top:12px">Could not read report file.</div>'

    summary: dict | None = None
    try:
        with summary_path.open(encoding="utf-8") as f:
            summary = json.load(f)
    except Exception:
        pass

    # Download links are shown only for files that were actually written for this run.
    dl_files = [
        (f"energy_ledger_{date_suffix}.csv", "Energy Ledger"),
        (f"billing_{date_suffix}.csv", "Billing CSV"),
        (f"billing_{date_suffix}.json", "Billing JSON"),
        (f"match_detail_{date_suffix}.csv", "Match Detail CSV"),
        (f"community_audit_{date_suffix}.csv", "Community Audit CSV"),
        (f"community_summary_{date_suffix}.json", "Community Summary JSON"),
    ]
    dl_links = " &nbsp;&middot;&nbsp; ".join(
        f'<a href="{ingress_path}/download?f={_html_escape(fn)}" '
        f'style="color:#2563eb;text-decoration:none">&#8659; {_html_escape(label)}</a>'
        for fn, label in dl_files
        if (path / fn).exists()
    )

    parts: list[str] = []
    if dl_links:
        parts.append(f'<div class="ts" style="margin-top:10px">Download: {dl_links}</div>')

    if summary:
        ok_color = "#16a34a" if summary.get("settlement_balance_ok") else "#dc2626"
        parts.append(
            f'<div class="grid" style="margin:16px 0 8px">'
            f'<div class="metric"><div class="val">{summary.get("self_consumption_ratio_pct", 0):.1f}%</div>'
            f'<div class="lbl">Self Consumption</div></div>'
            f'<div class="metric"><div class="val">{summary.get("autarky_ratio_pct", 0):.1f}%</div>'
            f'<div class="lbl">Autarky</div></div>'
            f'<div class="metric"><div class="val">{summary.get("local_shared_kwh", 0):.3f}</div>'
            f'<div class="lbl">Local Shared kWh</div></div>'
            f'<div class="metric"><div class="val" style="color:{ok_color}">'
            f'{"OK" if summary.get("settlement_balance_ok") else "ERR"}</div>'
            f'<div class="lbl">Balance</div></div>'
            f'</div>'
            f'<div class="ts">Period: {_html_escape(str(summary.get("period_start", "?")))}'
            f' &rarr; {_html_escape(str(summary.get("period_end", "?")))}</div>'
        )

    if not records:
        parts.append('<div class="ts" style="margin-top:12px">No billing records in this report.</div>')
        return "".join(parts)

    # Keep table styling local for now; moving it to a real template/static CSS is the next UI step.
    th_r = 'style="padding:6px 8px;text-align:right;background:#f5f7fa;border-bottom:2px solid #e5e7eb"'
    th_l = 'style="padding:6px 8px;text-align:left;background:#f5f7fa;border-bottom:2px solid #e5e7eb"'

    # Energy totals table: audit-friendly view of import/export and local/grid split.
    sum_exp = sum(r.get("total_export_kwh", 0) for r in records)
    sum_loc_s = sum(r.get("local_supplied_kwh", 0) for r in records)
    sum_grd_e = sum(r.get("grid_export_kwh", 0) for r in records)
    sum_imp = sum(r.get("total_import_kwh", 0) for r in records)
    sum_loc_r = sum(r.get("local_received_kwh", 0) for r in records)
    sum_grd_i = sum(r.get("grid_import_kwh", 0) for r in records)

    flow_rows = "".join(
        f'<tr style="border-bottom:1px solid #f0f0f0">'
        f'<td style="padding:6px 8px">{_html_escape(str(r.get("label", "")))}</td>'
        f'<td style="padding:6px 8px;text-align:right">{r.get("total_export_kwh", 0):.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{r.get("local_supplied_kwh", 0):.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{r.get("grid_export_kwh", 0):.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{r.get("total_import_kwh", 0):.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{r.get("local_received_kwh", 0):.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{r.get("grid_import_kwh", 0):.3f}</td>'
        f'</tr>'
        for r in records
    )
    flow_rows += (
        f'<tr style="font-weight:bold;border-top:2px solid #e5e7eb;background:#f9fafb">'
        f'<td style="padding:6px 8px">SUMME</td>'
        f'<td style="padding:6px 8px;text-align:right">{sum_exp:.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{sum_loc_s:.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{sum_grd_e:.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{sum_imp:.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{sum_loc_r:.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{sum_grd_i:.3f}</td>'
        f'</tr>'
    )
    parts.append(
        f'<div style="overflow-x:auto;margin-top:16px">'
        f'<div style="font-weight:600;font-size:.8rem;color:#6b7280;text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:6px">Summen je Zaehler</div>'
        f'<table style="width:100%;border-collapse:collapse;font-size:.85rem">'
        f'<thead><tr>'
        f'<th {th_l}>Zaehler</th>'
        f'<th {th_r}>Total Export</th>'
        f'<th {th_r}>Lokal verkauft</th>'
        f'<th {th_r}>Netzeinspeisung</th>'
        f'<th {th_r}>Total Import</th>'
        f'<th {th_r}>Lokal erhalten</th>'
        f'<th {th_r}>Netzbezug</th>'
        f'</tr></thead>'
        f'<tbody>{flow_rows}</tbody>'
        f'</table></div>'
    )

    # Cost table: participant-facing billing summary.
    cost_rows = "".join(
        f'<tr style="border-bottom:1px solid #f0f0f0">'
        f'<td style="padding:6px 8px">{_html_escape(str(r.get("label", "")))}</td>'
        f'<td style="padding:6px 8px;text-align:right">{r.get("local_received_kwh", 0):.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right">{r.get("grid_import_kwh", 0):.3f}</td>'
        f'<td style="padding:6px 8px;text-align:right"><strong>{r.get("total_cost_chf", 0):.4f} CHF</strong></td>'
        f'</tr>'
        for r in records
    )
    parts.append(
        f'<div style="overflow-x:auto;margin-top:16px">'
        f'<div style="font-weight:600;font-size:.8rem;color:#6b7280;text-transform:uppercase;'
        f'letter-spacing:.05em;margin-bottom:6px">Kosten je Teilnehmer</div>'
        f'<table style="width:100%;border-collapse:collapse;font-size:.85rem">'
        f'<thead><tr>'
        f'<th {th_l}>Teilnehmer</th>'
        f'<th {th_r}>Lokal erhalten kWh</th>'
        f'<th {th_r}>Netzbezug kWh</th>'
        f'<th {th_r}>Kosten CHF</th>'
        f'</tr></thead>'
        f'<tbody>{cost_rows}</tbody>'
        f'</table></div>'
    )

    return "".join(parts)


def build_reports_card(ingress_path: str, selected_stem: str) -> str:
    """Build the reports card HTML with dropdown and optional content."""
    report_list = list_billing_reports()

    if not report_list:
        return (
            '<div class="card"><h2>Settlement Reports</h2>'
            '<div class="ts">No reports yet - run the settlement engine first.</div></div>'
        )

    options = '<option value="">- Select report -</option>\n'
    for stem, label in report_list:
        sel = " selected" if stem == selected_stem else ""
        options += f'<option value="{_html_escape(stem)}"{sel}>{_html_escape(label)}</option>\n'

    report_content = ""
    if selected_stem:
        known = {s for s, _ in report_list}
        if selected_stem in known:
            report_content = _render_report_section(selected_stem, ingress_path)
        else:
            report_content = '<div class="error-box" style="margin-top:12px">Report not found.</div>'

    sel_style = (
        "flex:1;padding:8px 10px;border:1px solid #ddd;"
        "border-radius:4px;font-size:.9rem;background:white"
    )
    return (
        f'<div class="card"><h2>Settlement Reports</h2>'
        f'<form method="get" action="{ingress_path}/">'
        f'<div class="file-row">'
        f'<select name="report" style="{sel_style}">{options}</select>'
        f'<button type="submit">&#128196; View</button>'
        f'</div></form>'
        f'{report_content}'
        f'</div>'
    )
