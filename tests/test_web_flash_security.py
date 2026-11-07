# -*- coding: utf-8 -*-
"""Flash messages remain text, including imported data and real HTTP form errors."""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import Mock

import pytest
from markupsafe import Markup, escape

from shareomat.config import PathConfig, RuntimeConfig
from shareomat.database.auth import create_admin, create_session
from shareomat.database.sqlite import init_db
from shareomat.database.supplier_tariffs import list_supplier_tariffs
from shareomat.security.context import Principal, set_principal
from shareomat.web.pages import tariffs
from shareomat.web.rendering import RequestContext, render_page
from shareomat.web.server import _Handler
from shareomat.web.state import get_state
from tests.test_auth import Browser, _setup_admin, server


class NoticeParser(HTMLParser):
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.tags = []
        self.text = ""
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if self.depth:
            self.tags.append(tag)
            self.depth += 1
        elif tag == "div" and dict(attrs).get("class") in ("notice-ok", "notice-err"):
            self.depth = 1

    def handle_endtag(self, tag):
        if self.depth:
            self.depth -= 1

    def handle_data(self, data):
        if self.depth:
            self.text += data


def _render():
    return render_page("placeholders/coming_soon.html", RequestContext("GET", ""), "tariffs",
                       title="Test", description="Synthetic page")


@pytest.mark.parametrize("text", [
    '<script>alert("synthetic")</script>', '<img src=x onerror="alert(1)">',
    '</div><svg onload="alert(1)"><text>test</text></svg>',
    'A & B < C > D "quoted" \'single\' &lt;entity&gt;',
    Markup('<strong>not trusted</strong> & "quoted"'),
])
def test_flash_is_generally_escaped_as_text(text):
    get_state().set_flash(text, ok=False)
    html = _render()
    notice = NoticeParser(html)
    assert notice.text == str(text)
    assert notice.tags == []
    assert str(escape(str(text))) in html


@pytest.mark.parametrize("action", ["import", "toggle", "delete"])
def test_imported_operator_and_stored_product_names_are_text(tmp_path, monkeypatch, action):
    db = tmp_path / "test.db"
    init_db(db)
    get_state().register_db(db)
    payload = '<b data-x="a&b">Synthetic & operator</b>'
    product = '<img src=x onerror="alert(1)"> & product'
    data = json.loads((Path(__file__).parent / "fixtures/vnb_tariff_sample.json").read_text(encoding="utf-8"))
    data["dsoName"] = payload
    for index, item in enumerate(data["tariffs"]):
        item["tariffName"] = f"{product} {index}"
    monkeypatch.setattr(tariffs, "fetch_vnb_tariff_json", lambda url: data)
    tariffs.handle_post(RequestContext("POST", "", ["vnb", "import"], form={"url": "https://example.invalid/tariffs"}))
    if action != "import":
        stored = list_supplier_tariffs(db)[0]
        tariffs.handle_post(RequestContext("POST", "", ["vnb", str(stored.id), action]))
    html = tariffs.handle_get(RequestContext("GET", ""))
    notice = NoticeParser(html)
    assert notice.tags == []
    assert (payload if action == "import" else product) in notice.text
    assert str(escape(payload if action == "import" else product)) in html


@pytest.mark.parametrize("field", ["valid_from", "energy_rate_ht"])
def test_real_http_form_error_roundtrip_escapes_invalid_input(server, field):
    browser = _setup_admin(server)
    payload = '<b title="a&b">invalid & value</b>'
    form = {"source": "Synthetic", "product": "Synthetic", "energy_rate_ht": "10", "valid_from": "2027-01-01"}
    form[field] = payload
    form["csrf_token"] = browser.page_csrf("/tariffs")
    status, _, _ = browser.post("/tariffs/vnb/new", form)
    assert status == 303
    status, _, html = browser.get("/tariffs")
    assert status == 200
    notice = NoticeParser(html)
    assert payload in notice.text
    assert notice.tags == []
    assert str(escape(payload)) in html


def test_upload_notice_keeps_controlled_bold_without_interpreting_the_filename(tmp_path):
    paths = PathConfig(inbox=tmp_path / "inbox", archive=tmp_path / "archive",
                       reports=tmp_path / "reports", state=tmp_path / "state")
    get_state().register_runtime(RuntimeConfig(paths=paths))
    name = '<img src=x> & "quoted".csv'
    handler = object.__new__(_Handler)
    handler._save_file_upload = Mock(return_value=(get_state().csrf_token, name, 1234))
    handler._redirect = Mock()
    handler._handle_upload("", "multipart/form-data")

    html = _render()
    notice = NoticeParser(html)
    assert notice.tags == ["strong"]
    assert name in notice.text and "1,234 bytes" in notice.text
    assert "✓" in notice.text and "→ Eingang" in notice.text
    assert name not in html


def test_upload_failure_is_escaped_once_not_double_escaped(tmp_path):
    get_state().register_runtime(RuntimeConfig(paths=PathConfig(
        inbox=tmp_path / "inbox", archive=tmp_path / "archive",
        reports=tmp_path / "reports", state=tmp_path / "state",
    )))
    payload = '<bad-file> & "details"'
    handler = object.__new__(_Handler)
    handler._save_file_upload = Mock(side_effect=ValueError(payload))
    handler._redirect = Mock()
    handler._handle_upload("", "multipart/form-data")
    notice = NoticeParser(_render())
    assert notice.text == f"Upload fehlgeschlagen: {payload}"
    assert notice.tags == []


@pytest.mark.parametrize("upload", [False, True])
def test_session_persisted_flash_obeys_the_same_text_rule(tmp_path, upload):
    db = tmp_path / "test.db"
    init_db(db)
    get_state().register_db(db)
    user = create_admin(db, "synthetic", "Long phrase for test only 2026")
    _, session = create_session(db, user.id, remember=False)
    principal = Principal(user.id, user.username, "", "admin", session.id, session.csrf_token, session.auth_at)
    payload = '<strong>persisted</strong> & "text"'
    set_principal(principal)
    try:
        if upload:
            from shareomat.web.rendering import upload_flash_message
            get_state().set_flash(upload_flash_message(payload, 1234))
        else:
            get_state().set_flash(payload)
        notice = NoticeParser(_render())
        assert payload in notice.text
        assert notice.tags == (["strong"] if upload else [])
        assert NoticeParser(_render()).text == ""
    finally:
        set_principal(None)
