# -*- coding: utf-8 -*-
"""The billing preview names its data source and explains a disabled save button.

On 20.11.2026 a preview on "E-Mail / CSV" showed "no data" for November although the grid operator's
values were imported, and its disabled save button looked clickable. Since 0.6.34 the official SDAT
data is the only source (ADR-0012): there is no other source to warn about any more.
"""
from __future__ import annotations

from shareomat.database import billing
from shareomat.web.pages import billing as billing_page
from shareomat.web.rendering import RequestContext
from tests.test_sdat_import_and_billing import (  # noqa: F401  (leg is a fixture)
    DAY,
    _import,
    _official_day,
    leg,
)


def _page() -> str:
    return billing_page.handle_get(RequestContext(
        method="GET", ingress_path="", segments=["preview"],
        query={"period_start": DAY.isoformat(), "period_end": DAY.isoformat()}))


def test_disabled_save_button_is_explained(leg):
    html = _page()                                                  # no official data at all
    assert "disabled>Als Entwurf speichern" in html.replace(" disabled>", "disabled>")
    assert "Speichern ist erst möglich" in html


def test_preview_names_the_official_source(leg):
    db, runtime, tmp = leg
    _import(db, tmp, "month.xml", _official_day())
    assert billing.compute_billing_preview(db, runtime, DAY, DAY).line_items
    html = _page()
    assert "SDAT (EBL/Swisseldex)" in html and "Speichern ist erst möglich" not in html
    assert "E-Mail / CSV" not in html and "Messdaten-Quelle" not in html
