# -*- coding: utf-8 -*-
"""
Dashboard HTML rendering for the Home Assistant Ingress interface.
"""

from __future__ import annotations

import logging
from html import escape as _html_escape

from shareomat.ha.web_reports import build_reports_card
from shareomat.ha.web_state import get_state

logger = logging.getLogger(__name__)

_KNOWN_STATUS_CLASSES = {"ok", "error", "starting", "offline"}

_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Shareomat Ledger</title>
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
  <h1>Shareomat Ledger</h1>
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
  {warnings_html}
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
      <div class="ts" style="margin-top:8px">Accepted: .csv &nbsp;&middot;&nbsp; .xml &nbsp;&middot;&nbsp; .xlsx &nbsp;&nbsp;|&nbsp;&nbsp; Max 100 MB</div>
    </form>
  </div>
</body>
</html>
"""


def _build_meters_html() -> str:
    """Build the metering points info card HTML."""
    meters = get_state().meter_list
    if not meters:
        # Missing meter IDs is a configuration problem, so surface it before any upload/report UI.
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
        '<b>producer</b> = PV only &nbsp;&middot;&nbsp; '
        '<b>consumer</b> = consumption only &nbsp;&middot;&nbsp; '
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


def render_dashboard(ingress_path: str, selected_report: str) -> str:
    """Render the complete dashboard HTML.

    This is intentionally a pure renderer: request parsing and HTTP response
    handling stay in ingress.py, while report-specific rendering stays in
    web_reports.py.
    """
    shared = get_state()
    state = shared.get()
    status = state["status"]
    if status in _KNOWN_STATUS_CLASSES:
        status_class = status
    else:
        logger.warning("Unknown ingress status '%s'; using 'starting' CSS class", status)
        status_class = "starting"

    # Runtime errors stay visible until the next successful settlement run clears them.
    error_html = ""
    if state["last_error"]:
        error_html = (
            f'<div class="card"><div class="error-box">'
            f'Last error: {_html_escape(str(state["last_error"]))}</div></div>'
        )

    warnings_html = ""
    warnings = state.get("warnings", [])
    if warnings:
        items = "".join(f"<li>{_html_escape(str(w))}</li>" for w in warnings)
        warnings_html = (
            '<div class="card"><div class="error-box">'
            f'<strong>Startup warning</strong><ul style="margin:8px 0 0 18px">{items}</ul>'
            '</div></div>'
        )

    # Upload messages are one-shot feedback after POST /upload redirects back here.
    upload_msg, upload_ok = shared.pop_upload_result()
    upload_msg_html = ""
    if upload_msg:
        css_class = "notice-ok" if upload_ok else "notice-err"
        upload_msg_html = f'<div class="card"><div class="{css_class}">{upload_msg}</div></div>'

    return _HTML_TEMPLATE.format(
        status=_html_escape(str(status)),
        status_class=status_class,
        inbox_count=state["inbox_count"],
        report_count=state["report_count"],
        last_run=_html_escape(str(state["last_run"] or "-")),
        warnings_html=warnings_html,
        error_html=error_html,
        upload_msg_html=upload_msg_html,
        reports_html=build_reports_card(ingress_path, selected_report),
        meters_html=_build_meters_html(),
        ingress_path=ingress_path,
    )
