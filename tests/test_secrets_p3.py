# -*- coding: utf-8 -*-
"""Credentials outside the database (ADR-0012 pt. 10, P3).

One place loads the configuration (file < secrets file < environment); the
ENTSO-E token moves from the database into the secret store; no secret ever
reaches a repr, a log line, the web page or the Shareomat backup. All values
are synthetic test secrets.
"""
from __future__ import annotations

import os
import sqlite3
import zipfile
from types import SimpleNamespace

import pytest
import yaml

import main as app
import shareomat.secret_store as secret_store
from shareomat.config import (
    SECRET_SOURCE_ENVIRONMENT,
    SECRET_SOURCE_SECRETS_FILE,
    load_runtime_config,
)
from shareomat.database.audit import list_events
from shareomat.database.backup import create_backup
from shareomat.database.settings import set_setting
from shareomat.database.sqlite import init_db
from shareomat.secret_store import (
    ENTSOE_API_TOKEN,
    SecretStore,
    SecretStoreError,
    entsoe_api_token,
    move_database_secrets,
    register_fixed_secrets,
    resolve,
    store_path,
)
from shareomat.web.pages import external_data
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state

MQTT_SECRET = "TEST-MQTT-GEHEIM-7f3a"
SWISSELDEX_SECRET = "TEST-SX-GEHEIM-91c2"
SMTP_SECRET = "TEST-SMTP-GEHEIM-0b8d"
WAN_SECRET = "TEST-WAN-GEHEIM-55e1"
TOKEN_FILE = "TEST-ENTSOE-DATEI-2d4c"
TOKEN_STORE = "TEST-ENTSOE-STORE-a6f0"
TOKEN_DB = "TEST-ENTSOE-DB-c3b7"
ALL_SECRETS = (MQTT_SECRET, SWISSELDEX_SECRET, SMTP_SECRET, WAN_SECRET, TOKEN_FILE, TOKEN_STORE, TOKEN_DB)


def _config(tmp_path, **sections):
    path = tmp_path / "leg_config.yaml"
    raw = {"paths": {n: str(tmp_path / n) for n in ("inbox", "archive", "reports", "state")}, **sections}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return path


def _secrets(tmp_path, data):
    path = tmp_path / "secrets.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    if os.name == "posix":
        path.chmod(0o600)
    return path


# ── one place, fixed order ───────────────────────────────────────────────────


def test_secrets_file_and_environment_reach_the_runtime_config(tmp_path):
    config = _config(tmp_path, mqtt={"enabled": True, "broker": "b", "username": "u"},
                     swisseldex={"enabled": True, "username": "cem"})
    _secrets(tmp_path, {"mqtt": {"password": MQTT_SECRET}, "swisseldex": {"password": SWISSELDEX_SECRET},
                        "entsoe": {"api_token": TOKEN_FILE}})
    runtime = load_runtime_config(config, environ={"SHAREOMAT_WAN_PASSWORD": WAN_SECRET})
    assert runtime.mqtt.password == MQTT_SECRET and runtime.swisseldex.password == SWISSELDEX_SECRET
    assert runtime.wan.password == WAN_SECRET and runtime.entsoe.api_token == TOKEN_FILE
    assert runtime.secret_sources == {"mqtt.password": SECRET_SOURCE_SECRETS_FILE,
                                      "swisseldex.password": SECRET_SOURCE_SECRETS_FILE,
                                      "entsoe.api_token": SECRET_SOURCE_SECRETS_FILE,
                                      "wan.password": SECRET_SOURCE_ENVIRONMENT}
    assert runtime.swisseldex.enabled and runtime.mqtt.username == "u"      # the rest comes from the file


def test_environment_wins_over_the_secrets_file_which_wins_over_the_config_file(tmp_path, caplog):
    config = _config(tmp_path, smtp={"password": "aus-der-datei"})
    _secrets(tmp_path, {"smtp": {"password": "aus-geheimnisdatei"}})
    with caplog.at_level("WARNING"):
        assert load_runtime_config(config, environ={}).smtp.password == "aus-geheimnisdatei"
    assert "Credentials in the configuration file (smtp.password)" in caplog.text
    assert "aus-der-datei" not in caplog.text
    runtime = load_runtime_config(config, environ={"SHAREOMAT_SMTP_PASSWORD": SMTP_SECRET})
    assert runtime.smtp.password == SMTP_SECRET and runtime.secret_sources["smtp.password"] == SECRET_SOURCE_ENVIRONMENT


def test_secrets_file_readable_by_others_stops_the_start(tmp_path, monkeypatch):
    """Linux: chmod 644 is refused. (On other systems the mode bits are simulated.)"""
    config = _config(tmp_path)
    path = _secrets(tmp_path, {"mqtt": {"password": MQTT_SECRET}})
    if os.name == "posix":
        path.chmod(0o644)
    else:
        monkeypatch.setattr(secret_store, "os", SimpleNamespace(name="posix"))    # Windows reports 0o666
    with pytest.raises(SecretStoreError, match="für andere Benutzer lesbar") as exc:
        load_runtime_config(config, environ={})
    assert MQTT_SECRET not in str(exc.value)


@pytest.mark.skipif(os.name != "posix", reason="file modes are a Linux matter")
def test_private_secrets_file_is_accepted(tmp_path):
    _secrets(tmp_path, {"mqtt": {"password": MQTT_SECRET}})
    assert load_runtime_config(_config(tmp_path), environ={}).mqtt.password == MQTT_SECRET


def test_no_secret_in_any_repr(tmp_path):
    _secrets(tmp_path, {"mqtt": {"password": MQTT_SECRET}, "swisseldex": {"password": SWISSELDEX_SECRET},
                        "smtp": {"password": SMTP_SECRET}, "entsoe": {"api_token": TOKEN_FILE}})
    runtime = load_runtime_config(_config(tmp_path), environ={"SHAREOMAT_WAN_PASSWORD": WAN_SECRET})
    text = repr(runtime) + str(runtime) + repr(SecretStore(tmp_path / "s.json"))
    assert not [s for s in ALL_SECRETS if s in text]


# ── the store ────────────────────────────────────────────────────────────────


def test_store_roundtrip_and_only_known_keys(tmp_path):
    store = SecretStore(tmp_path / "secrets.json")
    assert store.get(ENTSOE_API_TOKEN) == ""
    store.set(ENTSOE_API_TOKEN, f"  {TOKEN_STORE}  ")
    assert store.get(ENTSOE_API_TOKEN) == TOKEN_STORE and store.has(ENTSOE_API_TOKEN)
    if os.name == "posix":
        assert (store.path.stat().st_mode & 0o777) == 0o600
    with pytest.raises(SecretStoreError, match="Unbekannter Schlüssel"):
        store.set("smtp.password", "x")
    assert store.delete(ENTSOE_API_TOKEN) and not store.delete(ENTSOE_API_TOKEN)
    assert [p.name for p in tmp_path.iterdir()] == ["secrets.json"]          # no temp file left


def test_environment_or_secrets_file_win_over_the_store(tmp_path):
    db = tmp_path / "shareomat.db"
    SecretStore(store_path(db)).set(ENTSOE_API_TOKEN, TOKEN_STORE)
    assert resolve(db, ENTSOE_API_TOKEN) == (TOKEN_STORE, "store")
    register_fixed_secrets({ENTSOE_API_TOKEN: (TOKEN_FILE, SECRET_SOURCE_SECRETS_FILE)})
    assert entsoe_api_token(db) == TOKEN_FILE
    register_fixed_secrets({ENTSOE_API_TOKEN: ("", "")})                  # not set there: the store counts
    assert entsoe_api_token(db) == TOKEN_STORE


# ── the token leaves the database ────────────────────────────────────────────


def _db_with_token(tmp_path, value=TOKEN_DB):
    db = tmp_path / "shareomat.db"
    init_db(db)
    set_setting(db, "entsoe_api_token", value)
    return db


def _token_rows(db) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute("SELECT COUNT(*) FROM settings WHERE setting_key = 'entsoe_api_token'").fetchone()[0]
    finally:
        conn.close()


def test_token_moves_from_the_database_into_the_store(tmp_path):
    db = _db_with_token(tmp_path)
    assert move_database_secrets(db) == [ENTSOE_API_TOKEN]
    assert _token_rows(db) == 0 and SecretStore(store_path(db)).get(ENTSOE_API_TOKEN) == TOKEN_DB
    [event] = list_events(db, entity="secrets")
    assert event.action == "moved" and TOKEN_DB not in event.detail
    assert move_database_secrets(db) == []                                  # once only


def test_a_token_already_in_the_store_wins_and_the_row_still_goes(tmp_path):
    db = _db_with_token(tmp_path)
    SecretStore(store_path(db)).set(ENTSOE_API_TOKEN, TOKEN_STORE)
    move_database_secrets(db)
    assert _token_rows(db) == 0 and SecretStore(store_path(db)).get(ENTSOE_API_TOKEN) == TOKEN_STORE


def test_the_row_stays_if_the_store_cannot_be_written(tmp_path, monkeypatch, caplog):
    db = _db_with_token(tmp_path)

    def broken(self, key, value):
        raise SecretStoreError("Datenträger voll")

    monkeypatch.setattr(SecretStore, "set", broken)
    with caplog.at_level("ERROR"):
        assert move_database_secrets(db) == []
    assert _token_rows(db) == 1 and "stays in the database" in caplog.text and TOKEN_DB not in caplog.text


def test_the_backup_holds_no_secret(tmp_path):
    db = _db_with_token(tmp_path)
    move_database_secrets(db)
    info = create_backup(db, tmp_path / "backups")
    with zipfile.ZipFile(info.path) as archive:
        assert "secrets.json" not in " ".join(archive.namelist())
        assert not [n for n in archive.namelist() if TOKEN_DB.encode() in archive.read(n)]


# ── "Externe Daten": status only, password again, audit without value ───────


@pytest.fixture
def page_db(tmp_path):
    db = tmp_path / "shareomat.db"
    init_db(db)
    get_state().register_db(db)
    return db


def _post(**form):
    return external_data.handle_post(RequestContext(method="POST", ingress_path="", segments=["entsoe_token"],
                                                    form=form))


def test_page_never_shows_the_token(page_db):
    SecretStore(store_path(page_db)).set(ENTSOE_API_TOKEN, TOKEN_STORE)
    html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "hinterlegt (hier eingegeben)" in html and TOKEN_STORE not in html
    assert 'type="password" id="entsoe_api_token"' in html


def test_token_set_and_delete_are_audited_without_the_value(page_db):
    _post(entsoe_api_token=TOKEN_STORE)
    assert SecretStore(store_path(page_db)).get(ENTSOE_API_TOKEN) == TOKEN_STORE
    _post(delete="1")
    assert not SecretStore(store_path(page_db)).has(ENTSOE_API_TOKEN)
    events = list_events(page_db, entity="secrets")
    assert [e.action for e in events] == ["deleted", "changed"] or [e.action for e in events] == ["changed", "deleted"]
    assert not [e for e in events if TOKEN_STORE in e.detail]


def test_a_token_from_the_addon_option_is_not_changeable_on_the_page(page_db):
    register_fixed_secrets({ENTSOE_API_TOKEN: (TOKEN_FILE, SECRET_SOURCE_SECRETS_FILE)})
    html = external_data.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "aus der Add-on-Option" in html and "Token ersetzen" not in html and TOKEN_FILE not in html
    with pytest.raises(FormError, match="nicht ändern"):
        _post(entsoe_api_token=TOKEN_STORE)


def test_changing_the_token_needs_the_password_again():
    from shareomat.security.permissions import required_permission
    from shareomat.web.server import _Handler
    assert required_permission("POST", "external_data", ["entsoe_token"]) == "secrets.change"
    assert "secrets.change" in _Handler._RECENT_AUTH_MESSAGES


# ── a real start with test secrets: none of them in the log ──────────────────


def test_no_secret_reaches_the_log_during_a_start(tmp_path, monkeypatch, caplog, capsys):
    config = _config(tmp_path, mqtt={"enabled": True, "broker": "127.0.0.1", "port": 1, "username": "u"},
                     smtp={"enabled": True, "host": "127.0.0.1", "port": 1, "username": "u", "from_address": "a@example.ch"},
                     swisseldex={"enabled": True, "host": "127.0.0.1", "port": 1, "username": "u"},
                     web={"enabled": False})
    _secrets(tmp_path, {"mqtt": {"password": MQTT_SECRET}, "swisseldex": {"password": SWISSELDEX_SECRET},
                        "smtp": {"password": SMTP_SECRET}, "entsoe": {"api_token": TOKEN_FILE}})
    db = tmp_path / "shareomat.db"
    init_db(db)
    set_setting(db, "entsoe_api_token", TOKEN_DB)
    monkeypatch.setattr(app, "_RUNTIME_CONFIG_PATH", config)
    monkeypatch.setenv("SHAREOMAT_DB_PATH", str(db))
    monkeypatch.setenv("SHAREOMAT_WAN_PASSWORD", WAN_SECRET)
    monkeypatch.setattr(app, "_wait_for_shutdown", lambda: None)
    with caplog.at_level("DEBUG"):
        app.main()
    output = caplog.text + capsys.readouterr().out
    assert "Secrets moved out of the database" in output and "Loading runtime config" in output
    assert not [s for s in ALL_SECRETS if s in output]
