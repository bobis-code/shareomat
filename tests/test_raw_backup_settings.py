# -*- coding: utf-8 -*-
"""Settings page "Rohdatensicherung" (ADR-0010): choice of the network storage, password again, audit log."""
from __future__ import annotations

from functools import partial

import pytest

from shareomat.core.collector import raw_backup as rb
from shareomat.database.audit import list_events
from shareomat.database.settings import get_operation_settings
from shareomat.leg_const import RAW_BACKUP_MODE_OFF, RAW_BACKUP_MODE_REQUIRED
from shareomat.security.permissions import required_permission
from shareomat.web.pages import settings as settings_page
from tests.test_auth import PASSWORD, Browser, _setup_admin, server  # noqa: F401  (fixture)
from tests.test_backup import _age_password_entry


@pytest.fixture
def storage(tmp_path, monkeypatch):
    root = tmp_path / "share"
    (root / "omv").mkdir(parents=True)
    (root / "local").mkdir()
    mounts = tmp_path / "mounts"

    def mount(mounted: bool) -> None:
        mounts.write_text(f"//192.0.2.10/sicherung {root / 'omv'} {'cifs' if mounted else 'autofs'} rw 0 0\n",
                          encoding="utf-8")

    mount(True)
    monkeypatch.setattr(settings_page, "network_mounts", partial(rb.network_mounts, root, mounts))
    monkeypatch.setattr(settings_page, "resolve_target", partial(rb.resolve_target, share_root=root, mounts_file=mounts))
    return root, mount


def _save(browser, **form):
    data = {"csrf_token": browser.page_csrf("/settings"), "raw_backup_mode": RAW_BACKUP_MODE_REQUIRED,
            "raw_backup_mount": "omv", "raw_backup_subdir": "swisseldex-rohdaten"}
    data.update(form)
    return browser.post("/settings/raw_backup", data)


def test_card_offers_only_network_storages_and_warns_while_off(server, storage):  # noqa: F811
    a = _setup_admin(server)
    status, _, html = a.get("/settings")
    assert status == 200 and "Rohdatensicherung (Swisseldex-Originale)" in html
    assert "Rohdatensicherung ist ausgeschaltet" in html
    assert 'value="omv"' in html and 'value="local"' not in html


def test_required_backup_is_saved_and_logged(server, storage):  # noqa: F811
    root, _ = storage
    a = _setup_admin(server)
    status, _, _ = _save(a)
    assert status == 303
    settings = get_operation_settings(server["db"])
    assert (settings.raw_backup_mode, settings.raw_backup_mount, settings.raw_backup_subdir) == \
        (RAW_BACKUP_MODE_REQUIRED, "omv", "swisseldex-rohdaten")
    events = {e.action: e for e in list_events(server["db"], entity="raw_backup")}
    assert set(events) == {"passed", "changed"}                      # the storage test ran before switching
    assert "Modus required" in events["changed"].detail and "vorher: Modus off" in events["changed"].detail
    assert "verbindungstest/test-" in events["passed"].detail
    assert len(list((root / "omv" / "swisseldex-rohdaten" / "verbindungstest").iterdir())) == 1
    _, _, html = a.get("/settings")
    assert str(root / "omv" / "swisseldex-rohdaten") in html and "Rohdatensicherung ist ausgeschaltet" not in html


def test_required_backup_on_an_unmounted_storage_is_refused(server, storage):  # noqa: F811
    _, mount = storage
    mount(False)
    a = _setup_admin(server)
    _save(a)
    assert get_operation_settings(server["db"]).raw_backup_mode == RAW_BACKUP_MODE_OFF
    assert "nicht eingebunden" in a.get("/settings")[2]


@pytest.mark.parametrize("form", [{"raw_backup_mount": "local"}, {"raw_backup_mount": "../etc"},
                                  {"raw_backup_subdir": "../ausserhalb"}, {"raw_backup_mode": "egal"}])
def test_invalid_choices_change_nothing(server, storage, form):  # noqa: F811
    a = _setup_admin(server)
    _save(a, **form)
    settings = get_operation_settings(server["db"])
    assert settings.raw_backup_mode == RAW_BACKUP_MODE_OFF and settings.raw_backup_mount == ""
    assert list_events(server["db"], entity="raw_backup") == []


def test_an_old_password_entry_must_be_confirmed(server, storage):  # noqa: F811
    a = _setup_admin(server)
    _age_password_entry(server)
    _save(a)
    assert get_operation_settings(server["db"]).raw_backup_mode == RAW_BACKUP_MODE_OFF
    assert "um die Rohdatensicherung zu ändern" in a.get("/settings")[2]
    _save(a, confirm_password=PASSWORD)
    assert get_operation_settings(server["db"]).raw_backup_mode == RAW_BACKUP_MODE_REQUIRED


def test_without_login_nothing_changes(server, storage):  # noqa: F811
    _setup_admin(server)
    stranger = Browser(server["port"])
    status, headers, _ = stranger.post("/settings/raw_backup", {"csrf_token": "x", "raw_backup_mode": "off"})
    assert headers["Location"].startswith("/login")
    assert list_events(server["db"], entity="raw_backup") == []


def test_test_button_writes_one_test_file_and_shows_the_steps(server, storage):  # noqa: F811
    root, _ = storage
    a = _setup_admin(server)
    a.post("/settings/raw_backup/test", {"csrf_token": a.page_csrf("/settings"), "raw_backup_mode": "off",
                                         "raw_backup_mount": "omv", "raw_backup_subdir": "swisseldex-rohdaten"})
    _, _, html = a.get("/settings")
    assert "Speichertest bestanden" in html and "Überschreiben wird verhindert" in html
    assert len(list((root / "omv" / "swisseldex-rohdaten" / "verbindungstest").iterdir())) == 1
    assert get_operation_settings(server["db"]).raw_backup_mode == RAW_BACKUP_MODE_OFF    # testing saves nothing
    assert [e.action for e in list_events(server["db"], entity="raw_backup")] == ["passed"]


def test_switching_on_is_refused_when_the_storage_test_fails(server, storage, monkeypatch):  # noqa: F811
    def refusing(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(rb, "_create_exclusive", refusing)
    a = _setup_admin(server)
    _save(a)
    assert get_operation_settings(server["db"]).raw_backup_mode == RAW_BACKUP_MODE_OFF
    html = a.get("/settings")[2]
    assert "Nicht umgeschaltet" in html and "Neue Datei schreiben" in html
    assert [e.action for e in list_events(server["db"], entity="raw_backup")] == ["failed"]


def test_permission_is_its_own():
    assert required_permission("POST", "settings", ["raw_backup"]) == "raw_backup.configure"
    assert required_permission("POST", "settings", ["raw_backup", "test"]) == "raw_backup.configure"
    assert required_permission("GET", "settings", []) == "view"
