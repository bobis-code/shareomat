# -*- coding: utf-8 -*-
"""Tests for the e-mail templates (core, storage and the "E-Mail-Vorlagen" page)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from shareomat.core.email_templates import (
    TEMPLATE_SPECS,
    TemplateError,
    common_values,
    document_values,
    fill_template,
    placeholders_in,
    validate_template,
    welcome_values,
)
from shareomat.database.audit import list_events
from shareomat.database.community import save_community
from shareomat.database.email_templates import get_email_template, list_email_templates, reset_email_template, \
    save_email_template
from shareomat.database.sqlite import init_db
from shareomat.models.community import Community
from shareomat.models.invoicing import DOC_TYPE_CREDIT, DOC_TYPE_INVOICE, BillingDocument
from shareomat.security.context import Principal, set_principal
from shareomat.security.permissions import required_permission
from shareomat.web.navigation import NAV_SECTIONS
from shareomat.web.pages import email_templates as page
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "shareomat.db"
    init_db(path)
    get_state().register_db(path)
    return path


@pytest.fixture
def principal():
    p = Principal(user_id=1, username="admin", display_name="Admin", role="admin", session_id=1, csrf_token="x",
                  auth_at=datetime.now(timezone.utc))
    set_principal(p)
    yield p
    set_principal(None)


def _get(*segments) -> RequestContext:
    return RequestContext(method="GET", ingress_path="", segments=list(segments), query={}, form={})


def _post(segments, form=None) -> RequestContext:
    form = dict(form or {})
    form["csrf_token"] = get_state().csrf_token
    return RequestContext(method="POST", ingress_path="", segments=list(segments), query={}, form=form)


def _doc(doc_type: str, **kw) -> BillingDocument:
    values = dict(billing_run_id=1, participant_id="p1", doc_type=doc_type, number="R-1",
                  amount_chf=Decimal("1234.5"), issue_date=date(2026, 12, 5), due_date=date(2027, 1, 4),
                  recipient_label="Erika Muster", period_start=date(2026, 11, 1), period_end=date(2026, 11, 30),
                  local_kwh=Decimal("213.25"))
    values.update(kw)
    return BillingDocument(**values)


_COMMON = common_values("LEG Musterdorf", "Max Vertreter", "leg@example.ch", "Erika Muster")


# ── Core ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("kind", list(TEMPLATE_SPECS))
def test_default_texts_are_valid_and_fully_filled(kind):
    spec = TEMPLATE_SPECS[kind]
    validate_template(kind, spec.default_subject, spec.default_body)
    if kind == "welcome":
        values = welcome_values(_COMMON, contract_version=5, contract_valid_from=date(2027, 1, 1),
                                joined_at=date(2027, 1, 1))
    else:
        values = document_values(_COMMON, _doc(DOC_TYPE_INVOICE if kind == "invoice" else DOC_TYPE_CREDIT))
    # Every placeholder a kind offers has a value, so nothing stays as "{...}".
    assert {name for name, _ in spec.placeholders} == set(values)
    subject, body = fill_template(spec.default_subject, spec.default_body, values)
    assert "{" not in subject + body
    assert "Erika Muster" in body and "Max Vertreter" in body


def test_welcome_mentions_the_contract_attachment():
    spec = TEMPLATE_SPECS["welcome"]
    assert "Herzlich willkommen" in spec.default_body
    assert "Im Anhang finden Sie den Gesellschaftsvertrag" in spec.default_body
    assert "Gesellschaftsvertrag" in spec.attachment


def test_unknown_placeholder_is_refused():
    with pytest.raises(TemplateError, match=r"\{faellig_am\}"):
        validate_template("credit", "Gutschrift", "Zahlbar bis {faellig_am}")   # only invoices have a due date
    with pytest.raises(TemplateError, match=r"\{Name\}"):
        validate_template("welcome", "Hallo", "Guten Tag {Name}")               # case matters


def test_subject_must_be_one_line_and_texts_not_empty_or_too_long():
    with pytest.raises(TemplateError, match="einzeilig"):
        validate_template("welcome", "Hallo\r\nBcc: x@example.ch", "Text")
    with pytest.raises(TemplateError, match="Betreff"):
        validate_template("welcome", "  ", "Text")
    with pytest.raises(TemplateError, match="Text"):
        validate_template("welcome", "Hallo", "\n\n")
    with pytest.raises(TemplateError, match="zu lang"):
        validate_template("welcome", "Hallo", "x" * 10_001)


def test_body_line_endings_are_normalised():
    _, body = validate_template("welcome", " Hallo ", "Zeile 1\r\nZeile 2\r")
    assert body == "Zeile 1\nZeile 2"


def test_template_syntax_is_never_executed():
    """Not Jinja: braces with code stay literal text."""
    subject, body = fill_template("{{ 7*7 }}", "{% for x in y %}{name.__class__}{name}", {"name": "Erika"})
    assert subject == "{{ 7*7 }}"
    assert body == "{% for x in y %}{name.__class__}Erika"
    assert placeholders_in("{a} {b} {a}") == ["a", "b"]


def test_filled_subject_never_contains_a_line_break():
    subject, body = fill_template("Hallo {name}", "Guten Tag {name}", {"name": "Erika\r\nBcc: x@example.ch\x00"})
    assert subject == "Hallo Erika Bcc: x@example.ch"
    assert "\n" not in body and "\x00" not in body


def test_missing_value_stays_visible():
    assert fill_template("{name}", "{gemeinschaft}", {"name": "Erika"}) == ("Erika", "{gemeinschaft}")


def test_document_values_for_invoice_and_credit():
    invoice = document_values(_COMMON, _doc(DOC_TYPE_INVOICE))
    assert invoice["betrag"] == "CHF 1'234.50"
    assert invoice["energie_kwh"] == "213.2"
    assert invoice["zeitraum"] == "01.11.2026 – 30.11.2026"
    assert invoice["faellig_am"] == "04.01.2027"
    credit = document_values(_COMMON, _doc(DOC_TYPE_CREDIT, number="G-1", due_date=None, period_start=None))
    assert "faellig_am" not in credit
    assert credit["zeitraum"] == "–"
    assert credit["belegnummer"] == "G-1"


def test_welcome_values_without_dates():
    values = welcome_values(_COMMON, contract_version=None, contract_valid_from=None, joined_at=None)
    assert (values["vertrag_version"], values["vertrag_gueltig_ab"], values["beitritt_per"]) == ("–", "–", "–")


# ── Storage ─────────────────────────────────────────────────────────────────


def test_defaults_without_stored_text(db_path):
    templates = list_email_templates(db_path)
    assert [t.kind for t in templates] == ["welcome", "invoice", "credit"]
    assert all(t.is_default for t in templates)


def test_save_and_reset_are_audited(db_path):
    saved = save_email_template(db_path, "invoice", "Rechnung {belegnummer}", "Guten Tag {name}\r\n")
    assert (saved.subject, saved.body, saved.is_default) == ("Rechnung {belegnummer}", "Guten Tag {name}", False)
    assert get_email_template(db_path, "welcome").is_default        # other kinds untouched
    reset = reset_email_template(db_path, "invoice")
    assert reset.is_default and reset.subject == TEMPLATE_SPECS["invoice"].default_subject
    actions = [e.action for e in list_events(db_path, entity="email_template")]
    assert sorted(actions) == ["reset", "saved"]


def test_invalid_text_is_not_stored(db_path):
    with pytest.raises(TemplateError):
        save_email_template(db_path, "welcome", "Hallo", "{unbekannt}")
    assert get_email_template(db_path, "welcome").is_default


def test_unknown_kind_is_refused(db_path):
    with pytest.raises(KeyError):
        save_email_template(db_path, "contract_representative_name", "x", "y")
    with pytest.raises(KeyError):
        get_email_template(db_path, "../welcome")


# ── Page ────────────────────────────────────────────────────────────────────


def test_menu_entry_and_permission():
    verwaltung = dict(NAV_SECTIONS)["VERWALTUNG"]
    assert ("email_templates", "E-Mail-Vorlagen") in verwaltung
    assert required_permission("POST", "email_templates", ["welcome"]) == "masterdata.edit"
    assert required_permission("GET", "email_templates", ["welcome"]) == "view"


def test_overview_lists_all_templates(db_path):
    html = page.handle_get(_get())
    for spec in TEMPLATE_SPECS.values():
        assert spec.label in html
    assert html.count("Standardtext") >= 3


def test_edit_page_shows_escaped_preview(db_path):
    save_community(db_path, Community(community_id="ZEV-TEST", name="<b>LEG</b> Musterdorf"))
    html = page.handle_get(_get("welcome"))
    assert "Willkommen in der &lt;b&gt;LEG&lt;/b&gt; Musterdorf" in html
    assert "<b>LEG</b>" not in html
    assert "{vertrag_version}" in html          # placeholder list
    assert "Erika Muster" in html               # synthetic preview participant


def test_unknown_kind_shows_overview_and_post_is_refused(db_path):
    assert "Anhang beim Versand" in page.handle_get(_get("nope"))
    with pytest.raises(FormError):
        page.handle_post(_post(["nope"], {"subject": "x", "body": "y"}))
    with pytest.raises(FormError):
        page.handle_post(_post(["welcome", "delete"]))


def test_post_saves_and_redirects_to_edit_page(db_path, principal, monkeypatch):
    monkeypatch.setattr(get_state(), "set_flash", lambda msg, ok=True: None)
    target = page.handle_post(_post(["welcome"], {"subject": "Hallo {name}", "body": "Willkommen {name}"}))
    assert target == "/email-templates/welcome"
    assert get_email_template(db_path, "welcome").subject == "Hallo {name}"
    assert "angepasst" in page.handle_get(_get())


def test_refused_text_is_shown_again_once(db_path, principal, monkeypatch):
    flashes = []
    monkeypatch.setattr(get_state(), "set_flash", lambda msg, ok=True: flashes.append((msg, ok)))
    page.handle_post(_post(["welcome"], {"subject": "Hallo", "body": "Mein langer Text {Name}"}))
    (message, ok), = flashes
    assert not ok and "{Name}" in message
    assert get_email_template(db_path, "welcome").is_default
    html = page.handle_get(_get("welcome"))
    assert "Mein langer Text {Name}" in html and "nicht gespeichert" in html
    assert "Mein langer Text" not in page.handle_get(_get("welcome"))   # only once


def test_reset_via_page(db_path, principal, monkeypatch):
    monkeypatch.setattr(get_state(), "set_flash", lambda msg, ok=True: None)
    save_email_template(db_path, "credit", "G", "Text")
    page.handle_post(_post(["credit", "reset"]))
    assert get_email_template(db_path, "credit").is_default
