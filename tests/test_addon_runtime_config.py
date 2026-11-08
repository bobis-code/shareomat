# -*- coding: utf-8 -*-
"""The add-on's generated configuration: what the real start path gives Shareomat."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from shareomat.config import load_runtime_config

ADDON_DIR = Path(__file__).resolve().parent.parent / "ha_addon"


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


def _install_emsomat(addon):
    addon.EMSOMAT_MANIFEST.write_text("{}", encoding="utf-8")


def _generate(addon, **options):
    addon.OPTIONS_PATH.write_text(json.dumps({"mqtt_host": "core-mosquitto", **options}), encoding="utf-8")
    addon.main()
    return yaml.safe_load(addon.CONFIG_OUTPUT.read_text(encoding="utf-8"))


def _hand_block(addon, node="mein-knoten"):
    addon.CONFIG_OUTPUT.write_text(yaml.safe_dump({"sparkplug": {"enabled": True, "emsomat_edge_node_id": node}}), encoding="utf-8")


def test_emsomat_on_same_home_assistant_connects_without_input(addon):
    _install_emsomat(addon)
    _generate(addon)
    sparkplug = load_runtime_config(addon.CONFIG_OUTPUT).sparkplug
    assert sparkplug.enabled is True
    assert sparkplug.emsomat_edge_node_id == "emsomat-node"
    assert sparkplug.group_id == "emsomat"


def test_without_emsomat_sparkplug_stays_off(addon):
    assert _generate(addon)["sparkplug"]["enabled"] is False


def test_auto_ignores_entered_values(addon):
    _install_emsomat(addon)
    section = _generate(addon, sparkplug={"mode": "auto", "node_id": "anders", "group_id": "anders"})["sparkplug"]
    assert (section["enabled"], section["emsomat_edge_node_id"], section["group_id"]) == (True, "emsomat-node", "emsomat")


def test_off_is_respected(addon):
    _install_emsomat(addon)
    assert _generate(addon, sparkplug={"mode": "off"})["sparkplug"]["enabled"] is False


def test_manual_uses_entered_values(addon):
    section = _generate(addon, sparkplug={"mode": "manual", "node_id": " haus-1 ", "group_id": "gruppe"})["sparkplug"]
    assert (section["enabled"], section["emsomat_edge_node_id"], section["group_id"]) == (True, "haus-1", "gruppe")


def test_unknown_mode_stays_off(addon):
    _install_emsomat(addon)
    assert _generate(addon, sparkplug={"mode": "egal"})["sparkplug"]["enabled"] is False


@pytest.mark.parametrize("value", ["kaputt", None, 3, []])
def test_malformed_section_falls_back_to_auto(addon, value):
    _install_emsomat(addon)
    assert _generate(addon, sparkplug=value)["sparkplug"]["emsomat_edge_node_id"] == "emsomat-node"


@pytest.mark.parametrize("bad", ["", "   ", "a/b", "a+", "#", "a b", "a\x00b", "x" * 65])
@pytest.mark.parametrize("field", ["node_id", "group_id"])
def test_invalid_manual_id_switches_only_sparkplug_off(addon, field, bad):
    values = {"mode": "manual", "node_id": "n", "group_id": "g", field: bad}
    config = _generate(addon, sparkplug=values)
    assert config["sparkplug"]["enabled"] is False
    assert config["sparkplug"]["emsomat_edge_node_id"] == ""
    assert config["mqtt"]["broker"] == "core-mosquitto"
    assert load_runtime_config(addon.CONFIG_OUTPUT).sparkplug.enabled is False


def test_hand_written_block_survives_auto(addon):
    _install_emsomat(addon)
    _hand_block(addon)
    section = _generate(addon)["sparkplug"]
    assert section == {"enabled": True, "emsomat_edge_node_id": "mein-knoten"}


def test_off_replaces_hand_written_block(addon):
    _install_emsomat(addon)
    _hand_block(addon)
    assert _generate(addon, sparkplug={"mode": "off"})["sparkplug"]["enabled"] is False


def test_later_installed_emsomat_is_picked_up_on_next_start(addon):
    assert _generate(addon)["sparkplug"]["enabled"] is False
    _install_emsomat(addon)
    assert _generate(addon)["sparkplug"]["enabled"] is True


def test_returning_to_auto_does_not_keep_old_manual_value(addon):
    _generate(addon, sparkplug={"mode": "manual", "node_id": "alt", "group_id": "g"})
    _install_emsomat(addon)
    section = _generate(addon, sparkplug={"mode": "auto"})["sparkplug"]
    assert (section["emsomat_edge_node_id"], section["group_id"]) == ("emsomat-node", "emsomat")


def test_wan_section_is_left_alone(addon):
    addon.CONFIG_OUTPUT.write_text(yaml.safe_dump({"wan": {"enabled": True, "broker": "b"}}), encoding="utf-8")
    assert _generate(addon)["wan"] == {"enabled": True, "broker": "b"}


def test_addon_manifest_offers_the_section(addon):
    manifest = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))
    assert manifest["options"]["sparkplug"] == {"mode": "auto", "node_id": "emsomat-node", "group_id": "emsomat"}
    assert manifest["schema"]["sparkplug"] == {"mode": "list(auto|manual|off)", "node_id": "str", "group_id": "str"}


@pytest.mark.parametrize("language", ["de", "en"])
def test_translations_cover_the_section(language):
    manifest = yaml.safe_load((ADDON_DIR / "config.yaml").read_text(encoding="utf-8"))
    text = (ADDON_DIR / "translations" / f"{language}.yaml").read_text(encoding="utf-8")
    fields = yaml.safe_load(text)["configuration"]["sparkplug"]["fields"]
    assert set(fields) == set(manifest["schema"]["sparkplug"])
