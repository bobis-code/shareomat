# -*- coding: utf-8 -*-
"""Welcome e-mail with the contract package (core.delivery.welcome_mail + contract page). Synthetic data only."""

from __future__ import annotations

import smtplib
from datetime import date

import pytest

from shareomat.config import PathConfig, RuntimeConfig, SmtpConfig
from shareomat.core.delivery.welcome_mail import WelcomeMailError, preview_welcome, send_welcome
from shareomat.database.audit import list_events
from shareomat.database.participant_contract import assign_contract, get_assignment
from shareomat.database.participants import update_participant
from shareomat.models.participant import Participant
from shareomat.web.pages import contract as contract_page
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state
from tests.test_email_sending import SMTP_OK, _attachments, _FakeHTML, fake_pdf, flashes, smtp_server  # noqa: F401
from tests.test_participant_workflow_0613 import _sign, _v1, db  # noqa: F401  (fixture)


@pytest.fixture
def joined(db, monkeypatch, tmp_path, smtp_server, fake_pdf):  # noqa: F811
    version = _v1(db, monkeypatch)
    assignment = assign_contract(db, "p2", version.id, version.tariff_id, joined_at=date(2026, 1, 1))
    for name in ("inbox", "archive", "reports", "state"):
        (tmp_path / name).mkdir()
    runtime = RuntimeConfig(paths=PathConfig(inbox=tmp_path / "inbox", archive=tmp_path / "archive",
                                             reports=tmp_path / "reports", state=tmp_path / "state"),
                            smtp=SmtpConfig(**SMTP_OK.__dict__))
    get_state().register_db(db)
    get_state().register_runtime(runtime)
    return assignment


def _get(*segments):
    return contract_page.handle_get(RequestContext(method="GET", ingress_path="", segments=list(segments)))


def _post(segments, **form):
    return contract_page.handle_post(RequestContext(method="POST", ingress_path="", segments=list(segments),
                                                    form={"csrf_token": get_state().csrf_token, **form}))


def _welcome(assignment, **form):
    return _post(["participants", str(assignment.id), "welcome"], **form)


def test_preview_shows_recipient_text_and_package(db, joined, smtp_server):  # noqa: F811
    html = _get("participants", str(joined.id), "welcome")
    assert "p2@example.ch" in html and "Willkommen in der Test-LEG" in html and "Guten Tag Haus 2" in html
    assert "Beitrittserklaerung_und_Gesellschaftsvertrag_v" in html and "01.01.2026" in html
    assert "Jetzt per E-Mail senden" in html and smtp_server.outbox == []
    assert "/welcome" in _get("participants", str(joined.id), "declaration")      # link from the declaration


def test_sending_attaches_the_package_and_marks_the_declaration_issued(db, joined, smtp_server, flashes):  # noqa: F811
    assert _welcome(joined, confirm="on") == f"/contract/participants/{joined.id}/welcome"
    (sent,) = smtp_server.outbox
    assert sent["to"] == ["p2@example.ch"]
    (name, content), = _attachments(sent["message"])
    assert name.startswith("Beitrittserklaerung_und_Gesellschaftsvertrag_v") and content.startswith(b"%PDF-")
    page = _FakeHTML.rendered[-1]
    assert "<p>Vertrag</p>" in page and "Haus 2" in page and "window.print()" not in page
    after = get_assignment(db, joined.id)
    assert after.declaration_issued_at == date.today() and after.declaration_sha256
    assert after.declaration_snapshot in page                             # frozen = exactly what was sent
    actions = [e.action for e in list_events(db, entity="assignment", entity_id=joined.id)]
    assert actions[:2] == ["welcome_sent", "welcome_attempt"]
    assert "gesendet" in flashes[-1][0] and flashes[-1][1]


def test_second_send_needs_resend(db, joined, smtp_server, flashes):  # noqa: F811
    _welcome(joined, confirm="on")
    _welcome(joined, confirm="on")
    assert len(smtp_server.outbox) == 1 and "bereits gesendet" in flashes[-1][0] and not flashes[-1][1]
    _welcome(joined, confirm="on", resend="on")
    assert len(smtp_server.outbox) == 2


def test_confirmation_and_mailbox_are_required(db, joined, smtp_server):  # noqa: F811
    with pytest.raises(FormError, match="geprüft"):
        _welcome(joined)
    get_state().runtime.smtp = SmtpConfig()
    with pytest.raises(FormError, match="ausgeschaltet"):
        _welcome(joined, confirm="on")
    assert smtp_server.outbox == []


def test_not_sent_when_signed_or_not_ready(db, joined):  # noqa: F811
    update_participant(db, "p2", Participant("p2", "Haus 2", address_line="Testweg 2", postal_code="9999",
                                             city="Testdorf"))                       # e-mail address removed
    with pytest.raises(WelcomeMailError, match="nicht unterschriftsreif"):
        preview_welcome(db, contract_page.welcome_package(joined.id))
    update_participant(db, "p2", Participant("p2", "Haus 2", email="p2@example.ch", address_line="Testweg 2",
                                             postal_code="9999", city="Testdorf"))
    _sign(db, joined.id, day=date(2025, 1, 1))
    with pytest.raises(WelcomeMailError, match="bereits unterschrieben"):
        preview_welcome(db, contract_page.welcome_package(joined.id))
    assert "Kann nicht gesendet werden" in _get("participants", str(joined.id), "welcome")


def test_unclear_outcome_is_resolved_by_looking_in_sent(db, joined, smtp_server, flashes):  # noqa: F811
    smtp_server.fail_send = smtplib.SMTPServerDisconnected("closed")
    _welcome(joined, confirm="on")
    assert "Ergebnis unklar" in flashes[-1][0]
    assert get_assignment(db, joined.id).declaration_issued_at is None
    html = _get("participants", str(joined.id), "welcome")
    assert "Ergebnis unklar" in html and "Im Postfach gefunden" in html
    smtp_server.fail_send = None
    _welcome(joined, confirm="on")                                        # refused without resend
    assert smtp_server.outbox == [] and "Gesendet" in flashes[-1][0]
    _welcome(joined, found_in_sent="on")
    assert get_assignment(db, joined.id).declaration_issued_at is not None
    assert "Ergebnis unklar" not in _get("participants", str(joined.id), "welcome")
    with pytest.raises(FormError, match="unklarem Ergebnis"):
        _welcome(joined, found_in_sent="on")


def test_failed_send_is_logged_and_issues_nothing(db, joined, smtp_server, flashes):  # noqa: F811
    smtp_server.fail_login = smtplib.SMTPAuthenticationError(535, b"no")
    _welcome(joined, confirm="on")
    assert "Nicht gesendet" in flashes[-1][0] and SMTP_OK.password not in flashes[-1][0]
    assert get_assignment(db, joined.id).declaration_issued_at is None
    assert [e.action for e in list_events(db, entity="assignment", entity_id=joined.id)][:2] == [
        "welcome_failed", "welcome_attempt"]


def test_pdf_of_the_package_and_print_page_keeps_its_button(db, joined, fake_pdf):  # noqa: F811
    name, data = contract_page.welcome_pdf(db, str(joined.id))
    assert name.endswith(".pdf") and data.startswith(b"%PDF-")
    assert "window.print()" in _get("participants", str(joined.id), "package", "print")
    with pytest.raises(WelcomeMailError):
        contract_page.welcome_pdf(db, "999")


def test_send_welcome_directly_double_click_safe(db, joined, smtp_server):  # noqa: F811
    package = contract_page.welcome_package(joined.id)
    send_welcome(db, SMTP_OK, package)
    with pytest.raises(WelcomeMailError, match="bereits gesendet"):
        send_welcome(db, SMTP_OK, contract_page.welcome_package(joined.id))
    assert len(smtp_server.outbox) == 1
