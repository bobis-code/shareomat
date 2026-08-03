# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/analysis.py

Purpose:
    "Analyse" admin page: server-rendered SVG charts of the local/grid
    settlement split (LEG-wide or per participant), sourced from
    shareomat.database.settlement_history — i.e. results already
    computed by the automatic settlement cycle, not a new computation.
    Optionally overlays market price forecasts.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Charts show one point per real settlement period (see
    settlement_history.py) — never an artificial daily bucket, since a
    settlement period can span anywhere from a day to a month depending
    on when files arrive. Bucketed day/week charts are only honest for
    raw meter_readings (real 15-minute data), not for this table.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from shareomat.database.participants import list_participants
from shareomat.database.price_forecasts import list_price_forecasts
from shareomat.database.settlement_history import aggregate_leg_local_grid, aggregate_participant_local_grid
from shareomat.web.rendering import RequestContext, render_page

_RANGE_DAYS = {"7d": 7, "30d": 30, "90d": 90}
_DEFAULT_RANGE = "30d"


def handle_get(ctx: RequestContext) -> str:
    """Render local/grid history charts (LEG-wide or per participant), optionally with a price overlay."""
    db_path = ctx.db_path
    range_key = ctx.query.get("range", _DEFAULT_RANGE)
    if range_key not in _RANGE_DAYS:
        range_key = _DEFAULT_RANGE
    days = _RANGE_DAYS[range_key]

    view = ctx.query.get("view", "leg")
    participant_id = ctx.query.get("participant_id", "")
    if view == "participant" and not participant_id:
        view = "leg"
    show_price = ctx.query.get("overlay") == "price"

    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)

    participants = []
    series: list[dict] = []
    if db_path is not None:
        participants = list_participants(db_path, include_inactive=False)
        if view == "participant":
            series = aggregate_participant_local_grid(db_path, participant_id, start, now)
        else:
            series = aggregate_leg_local_grid(db_path, start, now)

    price_series: list[dict] = []
    if show_price and db_path is not None:
        price_series = [
            {"period_start": f.period_start.isoformat(), "price_chf_kwh": float(f.forecast_price_chf_kwh)}
            for f in list_price_forecasts(db_path, limit=days + 10)
            if f.period_start >= start.date()
        ]
        price_series.reverse()  # list_price_forecasts is newest-first; charts read left-to-right

    return render_page(
        "analysis/view.html", ctx, "analysis",
        view=view, range_key=range_key, participant_id=participant_id, show_price=show_price,
        participants=participants, series=series, price_series=price_series,
    )
