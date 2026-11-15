# -*- coding: utf-8 -*-
"""Swisseldex (SX-1): add-on options -> runtime config, the "Externe Daten" card, off = unchanged."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from shareomat.config import SwisseldexConfig, load_runtime_config
from shareomat.core.collector.swisseldex_receiver import SwisseldexReceiver
from shareomat.database.sqlite import init_db
from shareomat.web.pages import external_data
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state
from tests.swisseldex_fake import FakeMailbox, session_factory

ADDON_DIR = Path(__file__).resolve().parent.parent / "ha_addon"
PASSWORD = "pw-geheim-123"


@pytest.fixture
def addon(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("generate_runtime_config", ADDON_DIR / "generate_runtime_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "OPTIONS_PATH", tmp_path / "options.json")
    monkeypatch.setattr(module, "CONFIG_OUTPUT", tmp_path / "leg_config.yaml")
    monkeypatch.setattr(module, "ENV_OUTPUT", tmp_path / "env")
    monkeypatch.setattr(module, "EMSOMAT_MANIFEST", tmp_path / "manifest.json")
    return module


def _generate(addon, **options):
    addon.OPTIONS_PATH.write_text(json.dumps({"mqtt_host": "core-mosquitto", **options}), encoding="utf-8")
    addon.main()
    return addon.CONFIG_OUTPUT


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    yield path
    get_state().register_swisseldex(None)


# ── add-on options -> runtime config ──


def test_existing_installation_without_section_keeps_swisseldex_off(addon):
    runtime = load_runtime_config(_generate(addon))
    assert runtime.swisseldex.enabled is False
    assert runtime.swisseldex.auto_sync is False
    assert runtime.swisseldex.host == "datahub.swisseldex.ch" and runtime.swisseldex.port == 21


def test_options_reach_the_runtime_config(addon):
    path = _generate(addon, swisseldex={
        "enabled": True, "host": "datahub.swisseldex.ch", "port": 21, "username": "cem-user",
        "password": PASSWORD, "auto_sync": True, "sync_interval_hours": "12",
    })
    runtime = load_runtime_config(path)
    assert runtime.swisseldex == SwisseldexConfig(
        enabled=True, username="cem-user", password=PASSWORD, auto_sync=True, sync_interval_hours=12,
    )


@pytest.mark.parametrize("interval", ["7", "abc", "0"])
def test_invalid_interval_falls_back_to_six_hours(addon, interval):
    runtime = load_runtime_config(_generate(addon, swisseldex={"enabled": True, "sync_interval_hours": interval}))
    assert runtime.swisseldex.sync_interval_hours == 6


def test_malformed_section_keeps_swisseldex_off(addon):
    runtime = load_runtime_config(_generate(addon, swisseldex="kaputt"))
    assert runtime.swisseldex.enabled is False


def test_addon_manifest_declares_password_field_and_defaults_off():
    manifest = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))
    assert manifest["options"]["swisseldex"]["enabled"] is False
    assert manifest["options"]["swisseldex"]["auto_sync"] is False
    assert manifest["options"]["swisseldex"]["sync_interval_hours"] == "6"
    assert manifest["schema"]["swisseldex"]["password"] == "password?"
    for lang in ("de", "en"):
        translation = yaml.safe_load((ADDON_DIR / "translations" / f"{lang}.yaml").read_text(encoding="utf-8"))
        assert set(translation["configuration"]["swisseldex"]["fields"]) == set(manifest["schema"]["swisseldex"])


def test_standalone_config_without_section_is_off(tmp_path):
    config = tmp_path / "leg_config.yaml"
    config.write_text(yaml.safe_dump({"paths": {
        "inbox": str(tmp_path / "in"), "archive": str(tmp_path / "ar"),
        "reports": str(tmp_path / "re"), "state": str(tmp_path / "st"),
    }}), encoding="utf-8")
    assert load_runtime_config(config).swisseldex == SwisseldexConfig()


# ── "Externe Daten" card ──


def test_page_without_swisseldex_explains_how_to_switch_it_on(db_path):
    html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "Swisseldex Datahub" in html and "Nicht aktiviert" in html
    assert "Jetzt synchronisieren" not in html


def test_page_with_receiver_shows_buttons_and_never_the_password(db_path, tmp_path):
    receiver = SwisseldexReceiver(
        SwisseldexConfig(enabled=True, username="cem-user", password=PASSWORD), tmp_path / "sx",
        session_factory=session_factory(FakeMailbox()),
    )
    receiver.run_now("test")
    get_state().register_swisseldex(receiver)
    html = external_data.handle_get(RequestContext(method="GET", ingress_path="/ingress/x"))
    assert "Verbindung testen" in html and "Jetzt synchronisieren" in html
    assert "/ingress/x/external-data/swisseldex_test" in html or "swisseldex_test" in html
    assert "Verbindung erfolgreich – 0 Datei(en) vorhanden." in html
    assert PASSWORD not in html and "cem-user" not in html


def test_post_test_runs_in_the_receiver_and_flashes_its_result(db_path, tmp_path):
    receiver = SwisseldexReceiver(
        SwisseldexConfig(enabled=True, username="cem-user", password=PASSWORD), tmp_path / "sx",
        session_factory=session_factory(FakeMailbox()),
    )
    receiver.start()
    get_state().register_swisseldex(receiver)
    try:
        assert external_data.handle_post(RequestContext(method="POST", ingress_path="", segments=["swisseldex_test"])) is None
        message, ok = get_state().pop_flash()
        assert ok and "Verbindung erfolgreich" in message
        # Right after the test, a sync must wait for the login gate: queued, not a second login.
        external_data.handle_post(RequestContext(method="POST", ingress_path="", segments=["swisseldex_sync"]))
        message, ok = get_state().pop_flash()
        assert ok and "Geplant" in message
    finally:
        receiver.stop()


def test_post_without_receiver_is_a_form_error(db_path):
    with pytest.raises(FormError):
        external_data.handle_post(RequestContext(method="POST", ingress_path="", segments=["swisseldex_sync"]))
