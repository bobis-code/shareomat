# -*- coding: utf-8 -*-
"""Automatic Swisseldex sync switched on "Externe Daten": stored, effective at once, add-on options as fallback."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import main as app
from shareomat.config import SwisseldexConfig
from shareomat.core.collector.swisseldex_receiver import SwisseldexReceiver
from shareomat.database.audit import list_events
from shareomat.database.settings import (
    SWISSELDEX_AUTO_SYNC_KEY,
    SWISSELDEX_INTERVAL_KEY,
    get_operation_settings,
    get_swisseldex_schedule,
    save_operation_settings,
    save_swisseldex_schedule,
    set_setting,
)
from shareomat.database.sqlite import init_db
from shareomat.models.settings import SwisseldexSchedule
from shareomat.web.pages import external_data
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state
from tests.swisseldex_fake import FakeMailbox, session_factory

PASSWORD = "pw-geheim-123"


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    get_state().pop_flash()
    yield path
    get_state().register_swisseldex(None)


def _receiver(tmp_path, box=None, **config) -> SwisseldexReceiver:
    settings = {"enabled": True, "username": "cem-user", "password": PASSWORD, **config}
    return SwisseldexReceiver(SwisseldexConfig(**settings), tmp_path / "sx",
                              session_factory=session_factory(box or FakeMailbox()))


def _post(form: dict) -> None:
    external_data.handle_post(RequestContext(method="POST", ingress_path="", segments=["swisseldex_schedule"],
                                             form=form))


def _page() -> str:
    return external_data.handle_get(RequestContext(method="GET", ingress_path=""))


# ── stored schedule vs. add-on options ──


def test_without_a_saved_schedule_the_addon_options_apply(db_path):
    assert get_swisseldex_schedule(db_path, True, 12) == SwisseldexSchedule(True, 12, from_addon_options=True)


def test_a_saved_schedule_wins_over_the_addon_options(db_path):
    save_swisseldex_schedule(db_path, False, 3)
    assert get_swisseldex_schedule(db_path, True, 12) == SwisseldexSchedule(False, 3)


def test_saving_refuses_an_interval_that_is_not_offered(db_path):
    with pytest.raises(ValueError):
        save_swisseldex_schedule(db_path, True, 7)
    assert get_swisseldex_schedule(db_path, False, 6).from_addon_options


@pytest.mark.parametrize("stored", ["7", "", "²", "٦", "6.0", "0x6"])
def test_a_tampered_stored_interval_keeps_the_addon_interval(db_path, stored):
    set_setting(db_path, SWISSELDEX_AUTO_SYNC_KEY, "1")
    set_setting(db_path, SWISSELDEX_INTERVAL_KEY, stored)
    assert get_swisseldex_schedule(db_path, False, 12) == SwisseldexSchedule(True, 12)


@pytest.mark.parametrize("stored", ["ja", "true", "", "2"])
def test_a_tampered_stored_switch_falls_back_to_the_addon_options(db_path, stored):
    set_setting(db_path, SWISSELDEX_AUTO_SYNC_KEY, stored)
    assert get_swisseldex_schedule(db_path, True, 3) == SwisseldexSchedule(True, 3, from_addon_options=True)


def test_saving_the_other_settings_keeps_the_addon_fallback(db_path):
    save_operation_settings(db_path, get_operation_settings(db_path))
    assert get_swisseldex_schedule(db_path, True, 12).from_addon_options


def test_main_starts_the_receiver_with_the_saved_schedule(db_path):
    runtime = SimpleNamespace(swisseldex=SwisseldexConfig(
        enabled=True, username="cem-user", password=PASSWORD, auto_sync=False, sync_interval_hours=6))
    assert app._swisseldex_config(runtime, db_path) == runtime.swisseldex
    save_swisseldex_schedule(db_path, True, 12)
    config = app._swisseldex_config(runtime, db_path)
    assert (config.auto_sync, config.sync_interval_hours) == (True, 12)
    assert config.username == "cem-user" and config.password == PASSWORD


# ── receiver: effective at once ──


def test_switching_on_starts_an_automatic_sync_at_once(db_path, tmp_path):
    box = FakeMailbox()
    box.put("a.xml", b"<x/>")
    receiver = _receiver(tmp_path, box)
    receiver.start()
    try:
        assert receiver.snapshot()["next_auto_at"] is None
        receiver.set_schedule(True, 24)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            last = receiver.snapshot()["last_run"]
            if last and last["finished_at"]:
                break
            time.sleep(0.05)
        assert last is not None and last["trigger"] == "auto" and last["kind"] == "sync"
        assert box.retr_calls == ["a.xml"]
        snapshot = receiver.snapshot()
        assert snapshot["auto_sync"] is True and snapshot["interval_hours"] == 24
        next_run = datetime.fromisoformat(snapshot["next_auto_at"])
        assert next_run - datetime.fromisoformat(last["started_at"]) == timedelta(hours=24)
    finally:
        receiver.stop()


def test_switching_off_drops_the_planned_retry_and_the_next_run(db_path, tmp_path):
    receiver = _receiver(tmp_path, auto_sync=True)
    receiver.run_now("sync")
    attempt = datetime.fromisoformat(receiver.snapshot()["last_run"]["started_at"])
    receiver._auto_retry_at = time.time() + 300      # a retry planned after a transient failure
    receiver.set_schedule(False, 6)
    assert receiver.snapshot()["next_auto_at"] is None and receiver.schedule() == (False, 6)
    receiver.set_schedule(True, 6)
    assert datetime.fromisoformat(receiver.snapshot()["next_auto_at"]) == attempt + timedelta(hours=6)


def test_receiver_refuses_an_interval_that_is_not_offered(tmp_path):
    receiver = _receiver(tmp_path)
    with pytest.raises(ValueError):
        receiver.set_schedule(True, 7)
    assert receiver.schedule() == (False, 6)


# ── "Externe Daten" card ──


def test_page_shows_the_switch_and_that_the_addon_value_applies(db_path, tmp_path):
    get_state().register_swisseldex(_receiver(tmp_path))
    html = _page()
    assert "swisseldex_schedule" in html and 'name="auto_sync"' in html and 'name="interval_hours"' in html
    assert "Automatisch abholen" in html and "Wert aus der Add-on-Konfiguration" in html
    assert PASSWORD not in html and "cem-user" not in html


def test_post_switches_on_stores_applies_and_logs(db_path, tmp_path):
    receiver = _receiver(tmp_path)
    get_state().register_swisseldex(receiver)
    _post({"auto_sync": "1", "interval_hours": "12"})
    message, ok = get_state().pop_flash()
    assert ok and "alle 12 Stunden" in message and "ohne Neustart" in message
    assert get_swisseldex_schedule(db_path, False, 6) == SwisseldexSchedule(True, 12)
    assert receiver.schedule() == (True, 12)
    events = list_events(db_path, entity="swisseldex")
    assert [e.detail for e in events] == ["vorher: aus; neu: ein, alle 12 h"]
    html = _page()
    assert "Wert aus der Add-on-Konfiguration" not in html
    assert "die Add-on-Konfiguration «aus» gilt nicht mehr" in html


def test_post_switching_off_warns_about_the_30_days(db_path, tmp_path):
    receiver = _receiver(tmp_path, auto_sync=True)
    get_state().register_swisseldex(receiver)
    _post({"interval_hours": "6"})
    message, ok = get_state().pop_flash()
    assert not ok and "30 Tage" in message
    assert receiver.schedule() == (False, 6)
    assert get_swisseldex_schedule(db_path, True, 6) == SwisseldexSchedule(False, 6)


@pytest.mark.parametrize("interval", ["7", "", "abc", "-6", "²", "٦", "6; DROP TABLE settings", "6" * 5000])
def test_post_with_an_invalid_interval_changes_nothing(db_path, tmp_path, interval):
    receiver = _receiver(tmp_path)
    get_state().register_swisseldex(receiver)
    with pytest.raises(FormError):
        _post({"auto_sync": "1", "interval_hours": interval})
    assert receiver.schedule() == (False, 6)
    assert get_swisseldex_schedule(db_path, False, 6).from_addon_options
    assert list_events(db_path, entity="swisseldex") == []


def test_post_switching_on_without_credentials_is_refused(db_path, tmp_path):
    receiver = _receiver(tmp_path, password="")
    get_state().register_swisseldex(receiver)
    with pytest.raises(FormError, match="Passwort fehlt"):
        _post({"auto_sync": "1", "interval_hours": "6"})
    assert receiver.schedule() == (False, 6)
    assert get_swisseldex_schedule(db_path, False, 6).from_addon_options


def test_post_without_receiver_is_a_form_error(db_path):
    with pytest.raises(FormError):
        _post({"auto_sync": "1", "interval_hours": "6"})
    assert get_swisseldex_schedule(db_path, False, 6).from_addon_options
