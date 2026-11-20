# -*- coding: utf-8 -*-
"""The billing preview says which meter data source it uses and warns when official SDAT data is left unused.

On 20.11.2026 a preview on "E-Mail / CSV" showed "no data" for November although the grid operator's
values were imported, and its disabled save button looked clickable.
"""
from __future__ import annotations

from shareomat.database import billing
from shareomat.web.pages import billing as billing_page
from shareomat.web.rendering import RequestContext
from tests.test_sdat_import_and_billing import (  # noqa: F401  (leg is a fixture)
    DAY,
    _import,
    _official_day,
    _use_sdat_source,
    leg,
)

_UNUSED = "offizielle Messdaten des Netzbetreibers (SDAT) vor"


def _page() -> str:
    return billing_page.handle_get(RequestContext(
        method="GET", ingress_path="", segments=["preview"],
        query={"period_start": DAY.isoformat(), "period_end": DAY.isoformat()}))


def test_csv_source_with_imported_sdat_data_warns_and_says_so(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "month.xml", _official_day())
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    assert not preview.official_source and preview.official_data_unused
    assert not any(_UNUSED in i for i in preview.availability.blocking_issues)   # CSV billing stays unchanged
    html = _page()
    assert "Datenquelle:" in html and "E-Mail / CSV" in html and _UNUSED in html


def test_disabled_save_button_is_explained(leg):
    html = _page()                                                  # CSV source, no data at all
    assert "disabled>Als Entwurf speichern" in html.replace(" disabled>", "disabled>")
    assert "Speichern ist erst möglich" in html


def test_sdat_source_is_shown_and_not_warned_about(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "month.xml", _official_day())
    _use_sdat_source(db)
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    assert preview.official_source and preview.line_items and not preview.official_data_unused
    html = _page()
    assert "SDAT (EBL/Swisseldex)" in html and "Speichern ist erst möglich" not in html


def test_csv_source_without_sdat_data_has_no_warning(leg):
    db, runtime, _ = leg
    assert not billing.compute_billing_preview(db, runtime, DAY, DAY).official_data_unused
