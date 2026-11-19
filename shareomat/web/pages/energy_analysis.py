# -*- coding: utf-8 -*-
"""
File: shareomat/web/pages/energy_analysis.py

Purpose:
    The two analysis pages on the grid operator's official E66 data
    (menu "Energie & Analyse → Analyse"):

    GET /analysis/sums      "Summen & Auswertung": per metering point and
                            period (Tag/Woche/Monat/Jahr/Frei), drawn and
                            fed in, completeness and checks, plus the
                            selection per day (or month)
    GET /analysis/values    "Messwerte": the values of one metering point per
                            quarter hour, hour or day, with gap rows, and the
                            deliveries behind one quarter hour (?detail=)

    Every number on the sums page links to the values page with the filters
    already set (metering point, period, direction).

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Read-only (permission "view"). All filters are GET parameters and are
    checked strictly: dates, known values for period/resolution/direction,
    limits for the period and the rows; a metering point id only selects
    among stored values (bound SQL parameter, never SQL text). Invalid
    input falls back to the default and is said so on the page.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from shareomat.database.sdat_analysis import (
    MAX_DAYS,
    MAX_SUM_DAYS,
    meter_choices,
    period_sums,
    point_values,
    quarter_hour_origin,
)
from shareomat.web.navigation import url_for
from shareomat.web.rendering import RequestContext, render_page

MONTH_NAMES = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober",
               "November", "Dezember")
WEEKDAYS = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")

SUM_PERIODS = (("tag", "Tag"), ("woche", "Woche"), ("monat", "Monat"), ("jahr", "Jahr"), ("frei", "Frei"))
VALUE_PERIODS = (("gestern", "Gestern"), ("7tage", "Letzte 7 Tage"), ("tag", "Bestimmter Tag"),
                 ("monat", "Dieser Monat"), ("vormonat", "Letzter Monat"), ("frei", "Frei von–bis"))
RESOLUTION_LABELS = (("15min", "15 Min."), ("hour", "Stunde"), ("day", "Tag"))
DIRECTION_LABELS = (("beide", "Bezug + Einspeisung"), ("bezug", "nur Bezug"), ("einspeisung", "nur Einspeisung"))
_DIRECTIONS = {"beide": ("consumption", "production"), "bezug": ("consumption",), "einspeisung": ("production",)}
SORTS = (("messpunkt", "Teilnehmer"), ("bezug", "Bezug ↓"), ("einspeisung", "Einspeisung ↓"),
         ("luecken", "Lücken zuerst"))
PAGE_SIZES = (25, 50, 100, 200)

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_METERING_POINT = re.compile(r"^[A-Za-z0-9]{1,50}$")


# ── Private helpers ─────────────────────────────────────────────────────────


def _today() -> date:
    return date.today()


def _date(value: str) -> date | None:
    if not _DATE.match(value or ""):
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if 2000 <= parsed.year <= 2100 else None


def _choice(value: str, allowed, default: str) -> str:
    keys = [key for key, _ in allowed]
    return value if value in keys else default


def _next_month(day: date) -> date:
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)


def _shift_months(day: date, months: int) -> date:
    index = day.year * 12 + day.month - 1 + months
    return date(index // 12, index % 12 + 1, 1)


def _metering_point(ctx: RequestContext, choices) -> str:
    """The requested point, if it is a well-formed id ("" = all)."""
    value = (ctx.query.get("mp") or "").strip()
    if not _METERING_POINT.match(value):
        return ""
    known = {c.metering_point_id.upper(): c.metering_point_id for c in choices}
    return known.get(value.upper(), value.upper())


def _sum_period(kind: str, anchor: date, free: tuple[date | None, date | None]) -> tuple[date, date, str]:
    """(start, end exclusive, title) of the sums page."""
    if kind == "tag":
        return anchor, anchor + timedelta(days=1), f"{WEEKDAYS[anchor.weekday()]} {anchor:%d.%m.%Y}"
    if kind == "woche":
        monday = anchor - timedelta(days=anchor.weekday())
        sunday = monday + timedelta(days=6)
        return monday, sunday + timedelta(days=1), f"KW {monday.isocalendar()[1]} · {monday:%d.%m.} – {sunday:%d.%m.%Y}"
    if kind == "jahr":
        return date(anchor.year, 1, 1), date(anchor.year + 1, 1, 1), str(anchor.year)
    if kind == "frei":
        start, last = free
        return start, last + timedelta(days=1), f"{start:%d.%m.%Y} – {last:%d.%m.%Y}"
    first = anchor.replace(day=1)
    return first, _next_month(first), f"{MONTH_NAMES[first.month - 1]} {first.year}"


def _shifted_anchor(kind: str, anchor: date, step: int) -> date:
    if kind == "tag":
        return anchor + timedelta(days=step)
    if kind == "woche":
        return anchor + timedelta(days=7 * step)
    if kind == "jahr":
        return date(anchor.year + step, 1, 1)
    return _shift_months(anchor, step)


def _value_period(kind: str, today: date, day: date | None,
                  free: tuple[date | None, date | None]) -> tuple[date, date]:
    """(start, end exclusive) of the values page."""
    if kind == "7tage":
        return today - timedelta(days=7), today
    if kind == "tag" and day:
        return day, day + timedelta(days=1)
    if kind == "monat":
        return today.replace(day=1), _next_month(today)
    if kind == "vormonat":
        first = _shift_months(today, -1)
        return first, today.replace(day=1)
    if kind == "frei" and free[0] and free[1]:
        return free[0], free[1] + timedelta(days=1)
    return today - timedelta(days=1), today


def _linker(ctx: RequestContext, route: str, params: dict):
    """link(**changes) -> URL of `route` with the current filters, changed; None drops a parameter."""
    base = url_for(ctx.ingress_path, route)

    def link(**changes) -> str:
        merged = {**params, **changes}
        query = {key: value for key, value in merged.items() if value not in (None, "")}
        return f"{base}?{urlencode(query)}" if query else base
    return link


def _values_link(ctx: RequestContext, point: str, start: date, end: date, direction: str = "beide") -> str:
    """The values page of one point and period (end exclusive), with a resolution that fits the period."""
    days = (end - start).days
    resolution = "15min" if days <= MAX_DAYS["15min"] else "day"
    query = {"mp": point, "zeitraum": "frei", "von": start.isoformat(),
             "bis": (end - timedelta(days=1)).isoformat(), "werte": direction, "aufl": resolution}
    return f"{url_for(ctx.ingress_path, 'analysis_values')}?{urlencode(query)}"


def _sort_points(points, sort: str):
    if sort == "bezug":
        return sorted(points, key=lambda p: p.flows.consumption_total, reverse=True)
    if sort == "einspeisung":
        return sorted(points, key=lambda p: p.flows.production_total, reverse=True)
    if sort == "luecken":
        return sorted(points, key=lambda p: (p.check.missing + p.check.mismatched), reverse=True)
    return points


# ── Public interface ────────────────────────────────────────────────────────


def sums_page(ctx: RequestContext) -> str:
    """ "Summen & Auswertung"."""
    today = _today()
    choices = meter_choices(ctx.db_path) if ctx.db_path else []
    point = _metering_point(ctx, choices)
    kind = _choice(ctx.query.get("zeitraum", ""), SUM_PERIODS, "monat")
    sort = _choice(ctx.query.get("sort", ""), SORTS, "messpunkt")
    anchor = _date(ctx.query.get("datum", "")) or today
    free = (_date(ctx.query.get("von", "")), _date(ctx.query.get("bis", "")))
    notice = ""
    if kind == "frei":
        if not (free[0] and free[1]) or free[1] < free[0]:
            free = (today.replace(day=1), today)
            notice = "Zeitraum unvollständig oder ungültig: es wird dieser Monat bis heute gezeigt."
        elif (free[1] - free[0]).days + 1 > MAX_SUM_DAYS:
            free = (free[1] - timedelta(days=MAX_SUM_DAYS - 1), free[1])
            notice = f"Höchstens {MAX_SUM_DAYS} Tage: der Zeitraum wurde gekürzt."
    start, end, title = _sum_period(kind, anchor, free)
    sums = period_sums(ctx.db_path, start, end, metering_point_id=point or None) if ctx.db_path else None

    params = {"zeitraum": kind, "datum": anchor.isoformat() if kind != "frei" else None, "mp": point,
              "sort": sort if sort != "messpunkt" else None,
              "von": free[0].isoformat() if kind == "frei" and free[0] else None,
              "bis": free[1].isoformat() if kind == "frei" and free[1] else None}
    link = _linker(ctx, "analysis_sums", params)
    return render_page(
        "analysis/sums.html", ctx, "analysis_sums",
        sums=sums, points=_sort_points(sums.points, sort) if sums else [], choices=choices, point=point,
        kind=kind, sort=sort, title=title, start=start, end=end, free=free, notice=notice,
        periods=SUM_PERIODS, sorts=SORTS, link=link,
        previous_link=link(datum=_shifted_anchor(kind, anchor, -1).isoformat()) if kind != "frei" else "",
        next_link=link(datum=_shifted_anchor(kind, anchor, 1).isoformat()) if kind != "frei" else "",
        values_link=lambda mp, a, b, direction="beide": _values_link(ctx, mp, a, b, direction),
        month_names=MONTH_NAMES, weekdays=WEEKDAYS, today=today,
    )


def values_page(ctx: RequestContext) -> str:
    """ "Messwerte" of one metering point."""
    today = _today()
    choices = meter_choices(ctx.db_path) if ctx.db_path else []
    point = _metering_point(ctx, choices) or (choices[0].metering_point_id if choices else "")
    kind = _choice(ctx.query.get("zeitraum", ""), VALUE_PERIODS, "gestern")
    day = _date(ctx.query.get("datum", ""))
    free = (_date(ctx.query.get("von", "")), _date(ctx.query.get("bis", "")))
    resolution = _choice(ctx.query.get("aufl", ""), RESOLUTION_LABELS, "15min")
    direction = _choice(ctx.query.get("werte", ""), DIRECTION_LABELS, "beide")
    gaps = ctx.query.get("luecken", "1") != "0"
    try:
        page_size = int(ctx.query.get("zeilen", "100"))
    except ValueError:
        page_size = 100
    page_size = page_size if page_size in PAGE_SIZES else 100
    try:
        page_number = max(1, min(int(ctx.query.get("seite", "1")), 100_000))
    except ValueError:
        page_number = 1

    notices = []
    if kind == "frei" and not (free[0] and free[1] and free[0] <= free[1]):
        notices.append("Zeitraum unvollständig oder ungültig: es wird gestern gezeigt.")
        kind = "gestern"
    if kind == "tag" and not day:
        day = today - timedelta(days=1)
    start, end = _value_period(kind, today, day, free)
    if (end - start).days > MAX_DAYS["day"]:
        start = end - timedelta(days=MAX_DAYS["day"])
        notices.append(f"Höchstens {MAX_DAYS['day']} Tage: der Zeitraum wurde gekürzt.")
    days = (end - start).days
    if days > MAX_DAYS[resolution]:
        coarser = "hour" if days <= MAX_DAYS["hour"] else "day"
        labels = dict(RESOLUTION_LABELS)
        notices.append(f"Bei «{labels[resolution]}» höchstens {MAX_DAYS[resolution]} Tage am Bildschirm: "
                       f"Auflösung «{labels[coarser]}».")
        resolution = coarser

    values = None
    if ctx.db_path and point:
        values = point_values(ctx.db_path, point, start, end, resolution=resolution,
                              directions=_DIRECTIONS[direction], today=today)
    rows = values.rows if values else []
    if not gaps:
        rows = [row for row in rows if row.has_values]
    pages = max(1, -(-len(rows) // page_size))
    page_number = min(page_number, pages)
    shown = rows[(page_number - 1) * page_size: page_number * page_size]

    origin = None
    detail = ctx.query.get("detail", "")
    if detail and ctx.db_path and point:
        try:
            slot = datetime.fromisoformat(detail)
        except ValueError:
            slot = None
        if slot is not None and slot.tzinfo is not None and slot.minute % 15 == 0 and not slot.second:
            origin = quarter_hour_origin(ctx.db_path, point, slot.astimezone(timezone.utc))

    params = {"mp": point, "zeitraum": kind, "datum": day.isoformat() if kind == "tag" and day else None,
              "von": free[0].isoformat() if kind == "frei" and free[0] else None,
              "bis": free[1].isoformat() if kind == "frei" and free[1] else None,
              "aufl": resolution, "werte": direction, "luecken": None if gaps else "0",
              "zeilen": page_size if page_size != 100 else None}
    link = _linker(ctx, "analysis_values", params)
    span = end - start
    previous_link = link(zeitraum="frei", datum=None, von=(start - span).isoformat(),
                         bis=(start - timedelta(days=1)).isoformat(), seite=None, detail=None)
    next_link = link(zeitraum="frei", datum=None, von=end.isoformat(),
                     bis=(end + span - timedelta(days=1)).isoformat(), seite=None, detail=None)
    sums_query = {"zeitraum": "frei", "von": start.isoformat(), "bis": (end - timedelta(days=1)).isoformat(),
                  "mp": point}
    selected = next((c for c in choices if c.metering_point_id.upper() == point.upper()), None)
    return render_page(
        "analysis/values.html", ctx, "analysis_values",
        values=values, rows=shown, row_count=len(rows), page_number=page_number, pages=pages,
        page_size=page_size, page_sizes=PAGE_SIZES, choices=choices, point=point, selected=selected,
        kind=kind, day=day, free=free, start=start, end=end, last_day=end - timedelta(days=1), resolution=resolution, direction=direction,
        gaps=gaps, notices=notices, origin=origin, detail=detail,
        periods=VALUE_PERIODS, resolutions=RESOLUTION_LABELS, directions=DIRECTION_LABELS, link=link,
        previous_link=previous_link, next_link=next_link,
        sums_link=f"{url_for(ctx.ingress_path, 'analysis_sums')}?{urlencode(sums_query)}",
        weekdays=WEEKDAYS, today=today,
    )
