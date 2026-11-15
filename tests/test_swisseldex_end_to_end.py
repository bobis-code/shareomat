# -*- coding: utf-8 -*-
"""SX-1 -> SX-2 -> SX-3 end to end: mailbox, sync, automatic import, card, official billing, correction notice."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import main as app
from shareomat.config import SwisseldexConfig
from shareomat.core.collector.swisseldex_receiver import KIND_SYNC, SwisseldexReceiver
from shareomat.database import billing
from shareomat.web.pages import external_data
from shareomat.web.rendering import RequestContext
from shareomat.web.state import get_state
from tests.sdat_fixtures import build
from tests.swisseldex_fake import FakeMailbox, session_factory
from tests.test_sdat_import_and_billing import DAY, START, _official_day, _use_sdat_source, leg  # noqa: F401


def _receiver(db, runtime, box):
    return SwisseldexReceiver(
        SwisseldexConfig(enabled=True, username="u", password="p"), runtime.paths.state / "swisseldex",
        session_factory=session_factory(box, "u", "p"), importer=app._swisseldex_importer(db, runtime),
    )


def test_sync_imports_and_billing_uses_the_official_split(leg):  # noqa: F811
    db, runtime, tmp = leg
    box = FakeMailbox()
    box.put("SE_E66_20261201.xml", build(_official_day()))
    receiver = _receiver(db, runtime, box)
    record = receiver.run_now(KIND_SYNC)
    # Default data source: received, but not imported automatically (a release must not be disturbed).
    assert record["downloaded"] == 1 and record["import"] == "-" and record["imported"] == 0
    get_state().register_swisseldex(receiver)
    try:
        html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    finally:
        get_state().register_swisseldex(None)
    assert "1 Datei(en) nicht importiert" in html and "erneut prüfen und importieren" in html
    assert [o.imported for o in receiver.import_now()] == [True]

    get_state().register_swisseldex(receiver)
    try:
        html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    finally:
        get_state().register_swisseldex(None)
    assert "importiert (E66)" in html and "100001-TEST01" in html
    assert "verwendet weiterhin die bisherigen Messdateien" in html

    _use_sdat_source(db)
    preview = billing.compute_billing_preview(db, runtime, DAY, DAY)
    assert {i.participant_id: i.local_received_kwh for i in preview.line_items}["test-load"] == Decimal("144")


def test_correction_after_release_warns_and_changes_nothing(leg):  # noqa: F811
    db, runtime, tmp = leg
    box = FakeMailbox()
    box.put("first.xml", build(_official_day()))
    receiver = _receiver(db, runtime, box)
    _use_sdat_source(db)
    assert receiver.run_now(KIND_SYNC)["imported"] == 1
    draft = billing.save_draft(db, billing.compute_billing_preview(db, runtime, DAY, DAY))
    billing.release_billing_run(db, draft.id, override_reason="Test", runtime=runtime)
    get_state().clear_warnings()

    box.put("correction.xml", build(_official_day(consumer_local="1.000", producer_local="1.000", status="5",
                                                  created=datetime(2026, 12, 10, tzinfo=timezone.utc), document_id="CORR")))
    record = receiver.run_now(KIND_SYNC)
    assert record["imported"] == 1
    warnings = get_state().get()["warnings"]
    assert any("Korrekturabrechnung" in w for w in warnings)
    assert billing.get_billing_run(db, draft.id).run.status == "released"
    get_state().clear_warnings()


def test_rejected_file_shows_its_reason_and_the_retry_button(leg):  # noqa: F811
    db, runtime, tmp = leg
    from tests.sdat_fixtures import eic
    box = FakeMailbox()
    box.put("foreign.xml", build(_official_day(receiver_eic=eic("12X-SOMEONEELSE"))))
    receiver = _receiver(db, runtime, box)
    _use_sdat_source(db)
    record = receiver.run_now(KIND_SYNC)
    assert record["not_imported"] == 1 and record["import"] == "warning"
    get_state().register_swisseldex(receiver)
    try:
        html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    finally:
        get_state().register_swisseldex(None)
    assert "nicht importiert" in html and "erneut prüfen und importieren" in html
