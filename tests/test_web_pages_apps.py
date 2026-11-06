# -*- coding: utf-8 -*-
"""Tests for the "Apps & Integrationen" page (shareomat.web.pages.apps) and the Emsomat install detection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import shareomat.web.pages.apps as apps_page
from shareomat.ha.emsomat_install import read_emsomat_install
from shareomat.models.integrations import EmsomatInstall
from shareomat.web.rendering import RequestContext
from shareomat.web.state import get_state


def _ctx() -> RequestContext:
    return RequestContext(method="GET", ingress_path="")


def _write_manifest(config_dir: Path, content: str) -> None:
    manifest = config_dir / "custom_components" / "emsomat" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(content, encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_online_getter():
    state = get_state()
    yield
    state._emsomat_online_getter = None


# ── Install detection ────────────────────────────────────────────────────

def test_missing_config_dir_means_unknown(tmp_path: Path) -> None:
    result = read_emsomat_install(tmp_path / "does-not-exist")
    assert result == EmsomatInstall(config_dir_found=False, installed=False)


def test_config_dir_without_emsomat(tmp_path: Path) -> None:
    result = read_emsomat_install(tmp_path)
    assert result == EmsomatInstall(config_dir_found=True, installed=False)


def test_installed_with_version(tmp_path: Path) -> None:
    _write_manifest(tmp_path, json.dumps({"domain": "emsomat", "version": "1.4.0"}))
    result = read_emsomat_install(tmp_path)
    assert result == EmsomatInstall(config_dir_found=True, installed=True, version="1.4.0")


def test_broken_manifest_still_counts_as_installed(tmp_path: Path) -> None:
    _write_manifest(tmp_path, "{not json")
    result = read_emsomat_install(tmp_path)
    assert result.installed is True
    assert result.version is None


# ── Page ─────────────────────────────────────────────────────────────────

def test_page_shows_installed_version_and_online(monkeypatch) -> None:
    monkeypatch.setattr(apps_page, "read_emsomat_install",
                        lambda: EmsomatInstall(config_dir_found=True, installed=True, version="1.4.0"))
    get_state().register_emsomat_online(lambda: True)

    html = apps_page.handle_get(_ctx())

    assert "installiert" in html
    assert "1.4.0" in html
    assert "online" in html
    assert "installieren (HACS)" not in html     # no install button once installed


def test_page_offers_install_when_missing(monkeypatch) -> None:
    monkeypatch.setattr(apps_page, "read_emsomat_install",
                        lambda: EmsomatInstall(config_dir_found=True, installed=False))
    get_state().register_emsomat_online(lambda: False)

    html = apps_page.handle_get(_ctx())

    assert "nicht installiert" in html
    assert "offline" in html
    assert "hacs_repository" in html


def test_page_standalone_without_sparkplug(monkeypatch) -> None:
    monkeypatch.setattr(apps_page, "read_emsomat_install",
                        lambda: EmsomatInstall(config_dir_found=False, installed=False))

    html = apps_page.handle_get(_ctx())

    assert "eigenständiger Betrieb" in html
    assert "Sparkplug-Kanal" in html
