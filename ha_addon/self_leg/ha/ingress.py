# -*- coding: utf-8 -*-
"""
File: self_leg/ha/ingress.py

Purpose:
    Minimal HTTP server for the Home Assistant Ingress interface.
    Serves a status dashboard showing engine state, last run, file counts,
    a "Run Now" button, and a file upload form for dropping meter data
    files directly into the engine inbox.

Part of:
    SELF LEG — Swiss LEG/ZEV Settlement Engine

Notes:
    Uses Python stdlib http.server — no external dependencies.
    State is read from IngressState which is updated by the main engine.

    HA Supervisor proxies requests to the ingress port (default 8099).
    The X-Ingress-Path header is respected for path prefix handling.

    Endpoints:
        GET  /          — Status dashboard HTML
        POST /run       — Trigger a settlement run, redirect back to dashboard
        POST /upload    — Upload a meter file (.csv / .xml / .xlsx) to inbox

    No authentication is performed — HA Supervisor handles auth externally.
    File uploads are limited to 100 MB and .csv / .xml / .xlsx extensions.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from html import escape as _html_escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# ── Shared state ──────────────────────────────────────────────────────────────


class IngressState:
    """Thread-safe shared state between the engine and the ingress server."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data = {
            "status": "starting",
            "last_run": "—",
            "inbox_count": 0,
            "report_count": 0,
            "last_error": "",
        }
        self._on_run: Callable[[], None] | None = None
        self._inbox_path: Path | None = None
        self._reports_path: Path | None = None
        self._meter_list: list[tuple[str, str]] = []  # (mpid, label)
        self._upload_msg: str = ""
        self._upload_ok: bool = True

    def update(self, **kwargs) -> None:
        """Update one or more state fields atomically (thread-safe)."""
        with self._lock:
            self._data.update(kwargs)

    def get(self) -> dict:
        """Return a snapshot of the current state as a plain dict (thread-safe)."""
        with self._lock:
            return dict(self._data)

    def register_on_run(self, callback: Callable[[], None]) -> None:
        """Register the function to call when the dashboard Run button is pressed."""
        self._on_run = callback

    def register_inbox(self, inbox: Path) -> None:
        """Register the inbox path so uploaded files can be saved there."""
        self._inbox_path = inbox

    def register_reports(self, reports: Path) -> None:
        """Register the reports path so the dashboard can list and display them."""
        self._reports_path = reports

    def register_meters(self, meters: list[tuple[str, str]]) -> None:
        """Register configured (mpid, label) pairs so the dashboard can show them."""
        self._meter_list = list(meters)

    def trigger_run(self) -> bool:
        """Start a settlement run in a daemon thread. Returns False if no callback registered."""
        if self._on_run is None:
            return False
        threading.Thread(target=self._on_run, name="self_leg-ingress-run", daemon=True).start()
        return True

    def set_upload_result(self, message: str, *, ok: bool = True) -> None:
        """Store a one-shot upload result message shown on the next dashboard load."""
        with self._lock:
            self._upload_msg = message
            self._upload_ok = ok

    def pop_upload_result(self) -> tuple[str, bool]:
        """Return and clear the pending upload result. Returns (message, is_ok)."""
        with self._lock:
            msg, ok = self._upload_msg, self._upload_ok
            self._upload_msg = ""
            self._upload_ok = True
            return msg, ok


# Module-level singleton so main.py and the server share the same state
_state = IngressState()


def get_state() -> IngressState:
    """Return the module-level IngressState singleton."""
    return _state


# ── Report helpers ────────────────────────────────────────────────────────────


def _list_billing_reports() -> list[tuple[str, str]]:
    """Return (stem, display_label) for each billing JSON report file, newest first."""
    path = _state._reports_path
    if path is None or not path.exists():
        return []
    stems = sorted(
        [f.stem for f in path.iterdir()
         if f.name.startswith("billing_") and f.name.endswith(".json")],
        reverse=True,
    )
    result = []
    for stem in stems:
        date_part = stem[len("billing_"):]  # "YYYYMMDD_HHMMSS"
        try:
            dt = datetime.strptime(date_part, "%Y%m%d_%H%M%S")
            label = dt.strftime("%Y-%m-%d  %H:%M")
        except ValueError:
            label = date_part
        result.append((stem, label))
    return result


def _render_report_section(selected_stem: str, ingress_path: str) -> str:
    """Return HTML with billing table and community KPIs for the selected billing report stem."""
    path = _state._reports_path
    if path is None:
        return ""
    billing_path = path / f"{selected_stem}.json"
    date_suffix = selected_stem[len("billing_"):]
    summary_path = path / f"community_summary_{date_suffix}.json"

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

    # Download links for all report files of this run
    date_suffix_dl = selected_stem[len("billing_"):]
    dl_files = [
        (f"energy_ledger_{date_suffix_dl}.csv",     "Energy Ledger"),
        (f"billing_{date_suffix_dl}.csv",           "Billing CSV"),
        (f"billing_{date_suffix_dl}.json",          "Billing JSON"),
        (f"match_detail_{date_suffix_dl}.csv",      "Match Detail CSV"),
        (f"community_audit_{date_suffix_dl}.csv",   "Community Audit CSV"),
        (f"community_summary_{date_suffix_dl}.json","Community Summary JSON"),
    ]
    dl_links = " &nbsp;·&nbsp; ".join(
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
            f' → {_html_escape(str(summary.get("period_end", "?")))}</div>'
        )

    if records:
        th_r = 'style="padding:6px 8px;text-align:right;background:#f5f7fa;border-bottom:2px solid #e5e7eb"'
        th_l = 'style="padding:6px 8px;text-align:left;background:#f5f7fa;border-bottom:2px solid #e5e7eb"'

        # ── Energy flow table (Summen je Zähler) ──
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
            f'letter-spacing:.05em;margin-bottom:6px">Summen je Zähler</div>'
            f'<table style="width:100%;border-collapse:collapse;font-size:.85rem">'
            f'<thead><tr>'
            f'<th {th_l}>Zähler</th>'
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

        # ── Cost table ──
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
    else:
        parts.append('<div class="ts" style="margin-top:12px">No billing records in this report.</div>')

    return "".join(parts)


def _build_reports_card(ingress_path: str, selected_stem: str) -> str:
    """Build the full reports card HTML with dropdown and optional report content."""
    report_list = _list_billing_reports()

    if not report_list:
        return (
            '<div class="card"><h2>Settlement Reports</h2>'
            '<div class="ts">No reports yet — run the settlement engine first.</div></div>'
        )

    options = '<option value="">— Select report —</option>\n'
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


# ── HTML template ─────────────────────────────────────────────────────────────

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>SELF LEG Ledger</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #f5f7fa; color: #333; padding: 24px; }}
    h1 {{ font-size: 1.5rem; margin-bottom: 24px; color: #1a1a2e; }}
    .card {{ background: white; border-radius: 8px; padding: 20px;
             box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 16px; }}
    .card h2 {{ font-size: .9rem; text-transform: uppercase; letter-spacing: .05em;
                color: #666; margin-bottom: 12px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px,1fr)); gap: 16px; }}
    .metric {{ text-align: center; }}
    .metric .val {{ font-size: 2rem; font-weight: 700; }}
    .metric .lbl {{ font-size: .8rem; color: #666; margin-top: 4px; }}
    .status-ok {{ color: #16a34a; }}
    .status-error {{ color: #dc2626; }}
    .status-starting {{ color: #d97706; }}
    .status-offline {{ color: #6b7280; }}
    .error-box {{ background: #fef2f2; border: 1px solid #fecaca;
                  border-radius: 6px; padding: 12px; font-size: .85rem; color: #b91c1c; }}
    .notice-ok  {{ background: #f0fdf4; border: 1px solid #bbf7d0;
                   border-radius: 6px; padding: 12px; font-size: .85rem; color: #166534; }}
    .notice-err {{ background: #fef2f2; border: 1px solid #fecaca;
                   border-radius: 6px; padding: 12px; font-size: .85rem; color: #b91c1c; }}
    button {{ margin-top: 8px; padding: 10px 24px; background: #2563eb; color: white;
              border: none; border-radius: 6px; cursor: pointer; font-size: 1rem; }}
    button:hover {{ background: #1d4ed8; }}
    .btn-secondary {{ background: #059669; }}
    .btn-secondary:hover {{ background: #047857; }}
    .file-row {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; margin-top: 4px; }}
    .file-row input[type=file] {{ flex: 1; font-size: .9rem; }}
    .ts {{ font-size: .85rem; color: #666; }}
  </style>
</head>
<body>
  <h1>SELF LEG Ledger</h1>
  <div class="card">
    <h2>System Status</h2>
    <div class="grid">
      <div class="metric">
        <div class="val status-{status_class}">{status}</div>
        <div class="lbl">Status</div>
      </div>
      <div class="metric">
        <div class="val">{inbox_count}</div>
        <div class="lbl">Inbox Files</div>
      </div>
      <div class="metric">
        <div class="val">{report_count}</div>
        <div class="lbl">Reports</div>
      </div>
    </div>
    <div class="ts" style="margin-top:12px">Last run: {last_run}</div>
  </div>
  {meters_html}
  {error_html}
  {upload_msg_html}
  {reports_html}
  <div class="card">
    <h2>Manual Control</h2>
    <form method="post" action="{ingress_path}/run">
      <button type="submit">&#9654; Run Now</button>
    </form>
  </div>
  <div class="card">
    <h2>Upload Meter File</h2>
    <form method="post" action="{ingress_path}/upload" enctype="multipart/form-data">
      <div class="file-row">
        <input type="file" name="file" accept=".csv,.xml,.xlsx" required>
        <button type="submit" class="btn-secondary">&#8679; Upload to Inbox</button>
      </div>
      <div class="ts" style="margin-top:8px">Accepted: .csv &nbsp;·&nbsp; .xml &nbsp;·&nbsp; .xlsx &nbsp;&nbsp;|&nbsp;&nbsp; Max 100 MB</div>
    </form>
  </div>
</body>
</html>
"""

_ALLOWED_UPLOAD_EXT = {".csv", ".xml", ".xlsx"}
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB


# ── HTTP Handler ──────────────────────────────────────────────────────────────


class _IngressHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args) -> None:
        # Route BaseHTTPRequestHandler access logs through our logger instead of stderr
        logger.debug("Ingress: " + fmt, *args)

    def _serve_download(self, query: str) -> None:
        """Serve a report file as a download. Only files in the reports directory are allowed."""
        params = parse_qs(query)
        filename = params.get("f", [""])[0]
        reports_path = _state._reports_path

        # Security: basename only, must be in the reports directory
        safe_name = Path(filename).name
        if not safe_name or reports_path is None:
            self.send_response(400)
            self.end_headers()
            return

        file_path = reports_path / safe_name
        if not file_path.exists() or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return

        suffix = file_path.suffix.lower()
        mime = {
            ".csv":  "text/csv; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(suffix, "application/octet-stream")

        data = file_path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        logger.debug("Ingress download: served %s (%d bytes)", safe_name, len(data))

    def _build_meters_html(self) -> str:
        """Build the metering points info card HTML."""
        meters = _state._meter_list
        if not meters:
            return (
                '<div class="card"><h2>Metering Points</h2>'
                '<div class="error-box">'
                '<strong>No meter IDs configured!</strong><br>'
                'Go to Add-on &rarr; Configuration &rarr; <code>metering_points</code> '
                'and enter the real meter IDs (MPID) from your energy provider. '
                'Without this the engine cannot process any data.'
                '</div></div>'
            )
        rows = "".join(
            f'<tr style="border-bottom:1px solid #f0f0f0">'
            f'<td style="padding:5px 8px;font-family:monospace;font-size:.82rem">{_html_escape(mpid)}</td>'
            f'<td style="padding:5px 8px;color:#555">{_html_escape(label)}</td>'
            f'</tr>'
            for mpid, label in meters
        )
        note = (
            '<div class="ts" style="margin-top:10px">'
            '&#9432;&nbsp; If uploaded files are skipped, the MPID in the file does not match '
            'any entry above &mdash; update <code>metering_points</code> in the add-on configuration.'
            '<br>Roles: '
            '<b>producer</b> = PV only &nbsp;·&nbsp; '
            '<b>consumer</b> = consumption only &nbsp;·&nbsp; '
            '<b>producer_consumer</b> = both (use this if unsure). '
            'At least one producer-type AND one consumer-type meter is required.'
            '</div>'
        )
        return (
            f'<div class="card"><h2>Metering Points</h2>'
            f'<table style="width:100%;border-collapse:collapse;font-size:.85rem">'
            f'<thead><tr style="background:#f5f7fa">'
            f'<th style="padding:5px 8px;text-align:left;border-bottom:2px solid #e5e7eb">MPID</th>'
            f'<th style="padding:5px 8px;text-align:left;border-bottom:2px solid #e5e7eb">Label</th>'
            f'</tr></thead><tbody>{rows}</tbody></table>'
            f'{note}</div>'
        )

    def _ingress_path(self) -> str:
        return self.headers.get("X-Ingress-Path", "").rstrip("/")

    def do_GET(self) -> None:
        """Serve the status dashboard (GET /) or a report file download (GET /download)."""
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/").endswith("/download"):
            self._serve_download(parsed.query)
            return
        state = _state.get()
        status = state["status"]
        status_class = status if status in ("ok", "error", "starting", "offline") else "starting"

        error_html = ""
        if state["last_error"]:
            error_html = (
                f'<div class="card"><div class="error-box">'
                f'Last error: {state["last_error"]}</div></div>'
            )

        upload_msg, upload_ok = _state.pop_upload_result()
        upload_msg_html = ""
        if upload_msg:
            css_class = "notice-ok" if upload_ok else "notice-err"
            upload_msg_html = (
                f'<div class="card"><div class="{css_class}">{upload_msg}</div></div>'
            )

        ingress_path = self._ingress_path()
        qs = parse_qs(urlparse(self.path).query)
        selected_report = qs.get("report", [""])[0]
        reports_html = _build_reports_card(ingress_path, selected_report)

        # Metering points card — warns if no meters configured
        meters_html = self._build_meters_html()

        html = _HTML_TEMPLATE.format(
            status=status,
            status_class=status_class,
            inbox_count=state["inbox_count"],
            report_count=state["report_count"],
            last_run=state["last_run"] or "—",
            error_html=error_html,
            upload_msg_html=upload_msg_html,
            reports_html=reports_html,
            meters_html=meters_html,
            ingress_path=ingress_path,
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        """Handle POST /run (trigger run) and POST /upload (file upload to inbox)."""
        path = urlparse(self.path).path.rstrip("/")
        if path.endswith("/run"):
            triggered = _state.trigger_run()
            if triggered:
                logger.info("Ingress: manual run triggered")
            self.send_response(303)
            self.send_header("Location", self._ingress_path() + "/")
            self.end_headers()
        elif path.endswith("/upload"):
            self._handle_upload()
        else:
            self.send_response(404)
            self.end_headers()

    def _handle_upload(self) -> None:
        """Save an uploaded meter data file to the engine inbox."""
        result = self._parse_file_upload()
        ingress_path = self._ingress_path()

        if result is None:
            logger.warning("Ingress upload: no valid file in request")
            _state.set_upload_result("Upload failed: no valid file received.", ok=False)
            self.send_response(303)
            self.send_header("Location", ingress_path + "/")
            self.end_headers()
            return

        filename, data = result
        ext = Path(filename).suffix.lower()

        if ext not in _ALLOWED_UPLOAD_EXT:
            logger.warning("Ingress upload: rejected file type '%s'", ext)
            _state.set_upload_result(
                f"Upload failed: unsupported file type '{ext}'. "
                f"Use .csv, .xml, or .xlsx.", ok=False,
            )
            self.send_response(303)
            self.send_header("Location", ingress_path + "/")
            self.end_headers()
            return

        inbox = _state._inbox_path
        if inbox is None:
            logger.error("Ingress upload: inbox path not registered")
            _state.set_upload_result("Upload failed: inbox path not configured.", ok=False)
            self.send_response(303)
            self.send_header("Location", ingress_path + "/")
            self.end_headers()
            return

        try:
            inbox.mkdir(parents=True, exist_ok=True)
            dest = inbox / filename
            dest.write_bytes(data)
            logger.info("Ingress upload: saved %s (%d bytes) → inbox", filename, len(data))
            # Refresh inbox count so the dashboard reflects the new file immediately
            try:
                _state.update(inbox_count=sum(1 for f in inbox.iterdir() if f.is_file()))
            except Exception:
                pass
            _state.set_upload_result(
                f"&#10003; Uploaded: <strong>{filename}</strong> ({len(data):,} bytes) → inbox",
                ok=True,
            )
        except Exception as exc:
            logger.error("Ingress upload: failed to save %s: %s", filename, exc)
            _state.set_upload_result(f"Upload failed: {exc}", ok=False)

        self.send_response(303)
        self.send_header("Location", ingress_path + "/")
        self.end_headers()

    def _parse_file_upload(self) -> tuple[str, bytes] | None:
        """Parse a multipart/form-data request. Returns (safe_filename, data) or None."""
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return None

        # Extract boundary from Content-Type header
        boundary = b""
        for segment in content_type.split(";"):
            seg = segment.strip()
            if seg.lower().startswith("boundary="):
                boundary = seg[9:].strip('"').encode()
                break
        if not boundary:
            return None

        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > _MAX_UPLOAD_BYTES:
            logger.warning("Ingress upload: invalid Content-Length %d", length)
            return None

        body = self.rfile.read(length)
        delimiter = b"--" + boundary

        for chunk in body.split(delimiter):
            if b"filename=" not in chunk:
                continue
            if b"\r\n\r\n" not in chunk:
                continue
            headers_raw, content = chunk.split(b"\r\n\r\n", 1)
            content = content.rstrip(b"\r\n-")
            for line in headers_raw.decode("utf-8", errors="ignore").splitlines():
                if "Content-Disposition" not in line or "filename=" not in line:
                    continue
                for token in line.split(";"):
                    token = token.strip()
                    if token.lower().startswith("filename="):
                        raw_name = token[9:].strip('"').strip("'").strip()
                        # Strip any path components to prevent path traversal
                        safe_name = Path(raw_name).name
                        if safe_name and content:
                            return safe_name, content
        return None


# ── Server thread ─────────────────────────────────────────────────────────────


class IngressServer(threading.Thread):
    """Daemon thread running the ingress HTTP server."""

    def __init__(self, port: int = 8099) -> None:
        super().__init__(name="self_leg-ingress", daemon=True)
        self._port = port
        self._server: HTTPServer | None = None

    def stop(self) -> None:
        """Gracefully shut down the HTTP server."""
        if self._server:
            self._server.shutdown()

    def run(self) -> None:
        """Thread main loop: start HTTPServer and serve until stop() is called."""
        try:
            self._server = HTTPServer(("0.0.0.0", self._port), _IngressHandler)
            logger.info("Ingress server listening on port %d", self._port)
            self._server.serve_forever()
        except Exception as exc:
            logger.error("Ingress server error: %s", exc)
