# -*- coding: utf-8 -*-
"""Contract notice by e-mail or letter (core.delivery.notice_mail, Checkliste → Mitteilung, Dokumente).

Synthetic data only (Haus 2, Testweg, @example.ch).
"""

from __future__ import annotations

from datetime import date

import pytest

from shareomat.config import PathConfig, RuntimeConfig, SmtpConfig
from shareomat.core.delivery.mail import MailError, SentMail
from shareomat.core.delivery.notice_mail import NoticeMailError, build_drafts, send_notices
from shareomat.database.audit import list_events
from shareomat.database.contract_notices import ContractNoticeError, list_notices, local_today, resolve_unclear
from shareomat.database.participant_contract import (
    assign_contract,
    list_assignments_for_version,
    mark_notified,
    record_departure,
)
from shareomat.database.participants import update_participant
from shareomat.models.participant import Participant
from shareomat.web.pages import checklist as checklist_page
from shareomat.web.pages import documents as documents_page
from shareomat.web.rendering import FormError, RequestContext
from shareomat.web.state import get_state
from tests.test_email_sending import SMTP_OK, _attachments, _FakeHTML, fake_pdf, flashes, smtp_server  # noqa: F401
from tests.test_participant_workflow_0613 import _sign, _successor, _v1, db  # noqa: F401  (fixture)
from tests.test_auth import server  # noqa: F401  (HTTP fixture)

CONTRACT_PAGE = "<p>Vertrag v2</p>"


def _fake_pdf(page_html: str) -> bytes:
    return b"%PDF-1.7 " + page_html.encode("utf-8")[:40]


class _Transport:
    def __init__(self, error: Exception | None = None):
        self.error, self.outbox = error, []

    def __call__(self, smtp, mail):
        if self.error is not None:
            raise self.error
        self.outbox.append(mail)
        return SentMail(message_id=f"<{len(self.outbox)}@example.ch>", to=mail.to, size_bytes=100)


@pytest.fixture
def v2(db, monkeypatch):  # noqa: F811
    """p2 and p3 signed v1; v2 is published, so both need a notice (synthetic data)."""
    v1 = _v1(db, monkeypatch)
    for p in ("p2", "p3"):
        _sign(db, assign_contract(db, p, v1.id, v1.tariff_id, joined_at=date(2026, 1, 1)).id)
    return _successor(db, monkeypatch, date(2026, 9, 1), date(2027, 6, 1))


def _ids(db, version):  # noqa: F811
    return {a.participant_id: a.id for a in list_assignments_for_version(db, version.id)}


def _send(db, version, ids, transport, method="email", day=None):  # noqa: F811
    return send_notices(db, SmtpConfig(**SMTP_OK.__dict__), version.id, ids, method=method,
                        notice_date=day or local_today(), recorded_by="admin", contract_page_html=CONTRACT_PAGE,
                        pdf_renderer=_fake_pdf, transport=transport)


# ── core: preview, send, letter ──


def test_preview_is_the_e_mail_with_the_contract_and_writes_nothing(db, v2):  # noqa: F811
    ids = _ids(db, v2)
    drafts = {d.participant_id: d for d in build_drafts(db, v2.id, list(ids.values()), method="email",
                                                        notice_date=local_today())}
    d = drafts["p2"]
    assert d.to == "p2@example.ch" and not d.refusal
    assert "Version 2" in d.subject and "01.06.2027" in d.subject and d.body.startswith("Guten Tag Haus 2")
    assert d.attachment_name == "Gesellschaftsvertrag_v2.pdf"
    assert list_notices(db) == [] and all(a.notified_at is None for a in list_assignments_for_version(db, v2.id))


def test_sending_files_the_e_mail_and_only_then_records_the_notice(db, v2):  # noqa: F811
    transport = _Transport()
    outcome = _send(db, v2, list(_ids(db, v2).values()), transport)
    assert len(outcome.done) == 2 and outcome.failed == [] and outcome.notes == []
    mail = transport.outbox[0]
    assert mail.to == "p2@example.ch" and mail.attachments[0].filename == "Gesellschaftsvertrag_v2.pdf"
    assert mail.attachments[0].data.startswith(b"%PDF-") and b"Vertrag v2" in mail.attachments[0].data
    stored = {a.participant_id: a for a in list_assignments_for_version(db, v2.id)}
    assert stored["p2"].notified_at == local_today() and stored["p2"].notice_method == "email"
    assert "<1@example.ch>" in stored["p2"].notice_evidence and stored["p2"].notice_recorded_by == "admin"
    notices = list_notices(db)
    assert {n.status for n in notices} == {"sent"} and all(n.pdf_sha256 for n in notices)
    actions = [e.action for e in list_events(db, entity="participant", entity_id="p2")]
    assert actions.index("notice_email_sent") < actions.index("notice_email_attempt")      # newest first
    assert "notified" in actions
    assert _send(db, v2, list(_ids(db, v2).values()), transport).done == []          # never twice
    assert len(transport.outbox) == 2


def test_a_refused_login_records_nothing_and_stops(db, v2):  # noqa: F811
    transport = _Transport(MailError("Anmeldung abgelehnt — App-Passwort prüfen."))
    outcome = _send(db, v2, list(_ids(db, v2).values()), transport)
    assert outcome.done == [] and outcome.stopped
    assert [pid for pid, _ in outcome.failed] == ["p2", "p3"] and "angehalten" in outcome.failed[1][1]
    assert all(a.notified_at is None for a in list_assignments_for_version(db, v2.id))
    (notice,) = list_notices(db)
    assert notice.status == "failed"
    assert len(_send(db, v2, list(_ids(db, v2).values()), _Transport()).done) == 2   # a failure does not block


def test_an_unknown_outcome_blocks_a_second_e_mail_until_resolved(db, v2):  # noqa: F811
    ids = _ids(db, v2)
    outcome = _send(db, v2, [ids["p2"]], _Transport(MailError("Verbindung abgebrochen", uncertain=True)))
    assert "Ergebnis unklar" in outcome.failed[0][1]
    (notice,) = list_notices(db)
    assert notice.outcome_unknown
    transport = _Transport()
    again = _send(db, v2, [ids["p2"]], transport)
    assert transport.outbox == [] and "kein Ergebnis" in again.failed[0][1]
    assert resolve_unclear(db, notice.id, found_in_sent=True, recorded_by="admin") == ""
    p2 = {a.participant_id: a for a in list_assignments_for_version(db, v2.id)}["p2"]
    assert p2.notified_at == local_today() and "Gesendet" in p2.notice_evidence
    with pytest.raises(ContractNoticeError):
        resolve_unclear(db, notice.id, found_in_sent=False, recorded_by="admin")


def test_not_found_in_sent_allows_sending_again(db, v2):  # noqa: F811
    ids = _ids(db, v2)
    _send(db, v2, [ids["p2"]], _Transport(MailError("Verbindung abgebrochen", uncertain=True)))
    resolve_unclear(db, list_notices(db)[0].id, found_in_sent=False, recorded_by="admin")
    assert len(_send(db, v2, [ids["p2"]], _Transport()).done) == 1


def test_a_letter_is_created_with_the_chosen_date_and_no_e_mail(db, v2, monkeypatch):  # noqa: F811
    transport = _Transport()
    outcome = _send(db, v2, list(_ids(db, v2).values()), transport, method="letter", day=date(2027, 1, 10))
    assert transport.outbox == [] and len(outcome.done) == 2
    notice = next(n for n in outcome.done if n.participant_id == "p2")
    assert notice.status == "created" and notice.recipient.splitlines() == ["Haus 2", "Testweg 2", "9999 Testdorf"]
    p2 = {a.participant_id: a for a in list_assignments_for_version(db, v2.id)}["p2"]
    assert p2.notified_at == date(2027, 1, 10) and p2.notice_method == "letter"
    pages = []
    monkeypatch.setattr(documents_page, "render_pdf", lambda html: pages.append(html) or b"%PDF-1.7")
    name, data = documents_page.notice_pdf(db, str(notice.id))
    assert name == f"Vertragsmitteilung_v2_Nr{notice.id}.pdf" and data.startswith(b"%PDF-")
    page = pages[-1]
    assert "Haus 2" in page and "Testweg 2" in page and "Test Vertretung" in page and "10.01.2027" in page
    assert "Guten Tag Haus 2" in page and "<p>Vertrag</p>" in page and "Beilage: Gesellschaftsvertrag Version 2" in page
    assert "window.print()" not in page


def test_same_rules_as_the_manual_entry(db, v2, monkeypatch):  # noqa: F811
    ids = _ids(db, v2)
    mark_notified(db, ids["p2"], date(2027, 1, 5), method="letter")
    update_participant(db, "p3", Participant("p3", "Haus 3", email="", address_line="Testweg 2", postal_code="9999",
                                       city="Testdorf"))
    drafts = {d.participant_id: d for d in build_drafts(db, v2.id, list(ids.values()), method="email",
                                                        notice_date=local_today())}
    assert "bereits erfasst" in drafts["p2"].refusal and "keine E-Mail-Adresse" in drafts["p3"].refusal
    letters = {d.participant_id: d for d in build_drafts(db, v2.id, [ids["p3"]], method="letter",
                                                         notice_date=date(2027, 1, 10))}
    assert letters["p3"].refusal == ""
    record_departure(db, ids["p3"], date(2027, 3, 31), reason="Wegzug")
    (left,) = build_drafts(db, v2.id, [ids["p3"]], method="letter", notice_date=date(2027, 1, 10))
    assert "ausgetreten" in left.refusal


def test_unknown_method_and_unpublished_version_are_refused(db, v2):  # noqa: F811
    with pytest.raises(NoticeMailError):
        build_drafts(db, v2.id, [1], method="other", notice_date=local_today())
    with pytest.raises(NoticeMailError):
        build_drafts(db, 999, [1], method="email", notice_date=local_today())


# ── web: Checkliste and Dokumente ──


@pytest.fixture
def web(db, v2, tmp_path, smtp_server, fake_pdf):  # noqa: F811
    for name in ("inbox", "archive", "reports", "state"):
        (tmp_path / name).mkdir()
    get_state().register_db(db)
    get_state().register_runtime(RuntimeConfig(
        paths=PathConfig(inbox=tmp_path / "inbox", archive=tmp_path / "archive", reports=tmp_path / "reports",
                         state=tmp_path / "state"),
        smtp=SmtpConfig(**SMTP_OK.__dict__)))
    return v2


def _post(**form):
    return checklist_page.handle_post(RequestContext(method="POST", ingress_path="", segments=["batch"],
                                                     form={"kind": "notice", **form}))


def _selection(db, version, method="email", day=None):  # noqa: F811
    form = {"version_id": str(version.id), "notice_method": method,
            "batch_date": (day or local_today()).isoformat()}
    form.update({f"sel_{i}": "on" for i in _ids(db, version).values()})
    return form


def _page(location: str) -> str:
    query = dict(part.split("=", 1) for part in location.split("?", 1)[1].split("#")[0].split("&"))
    return checklist_page.handle_get(RequestContext(method="GET", ingress_path="", query=query))


def test_form_has_no_evidence_field_and_two_buttons(db, web):  # noqa: F811
    html = _page(f"/checklist?batch=notice&v={web.id}")
    assert 'name="step" value="preview"' in html and 'name="step" value="send"' in html
    assert 'name="evidence"' not in html.split('id="sammel"')[1].split("</form>")[0]
    assert "anderer Weg" not in html.split('id="sammel"')[1].split("</form>")[0]


def test_preview_page_shows_each_e_mail_and_sends_nothing(db, web, smtp_server):  # noqa: F811
    location = _post(step="preview", **_selection(db, web))
    html = _page(location)
    assert "Vorschau — noch nichts gesendet" in html and "p2@example.ch" in html and "p3@example.ch" in html
    assert "Guten Tag Haus 2" in html and "Gesellschaftsvertrag_v2.pdf" in html and "Senden (2)" in html
    assert "Test an mich senden" in html and "/checklist/notice-pdf?t=" in html
    assert smtp_server.outbox == [] and list_notices(db) == []


def test_send_from_the_preview_files_under_documents(db, web, smtp_server, flashes):  # noqa: F811
    location = _post(step="preview", **_selection(db, web))
    token = location.split("&t=")[1]
    assert _post(step="send", version_id=str(web.id), token=token) == "/documents#mitteilungen"
    assert sorted(m["to"][0] for m in smtp_server.outbox) == ["p2@example.ch", "p3@example.ch"]
    (name, content), = _attachments(smtp_server.outbox[0]["message"])
    assert name == "Gesellschaftsvertrag_v2.pdf" and content.startswith(b"%PDF-")
    assert "2 gesendet und eingetragen" in flashes[-1][0] and flashes[-1][1]
    html = documents_page.handle_get(RequestContext(method="GET", ingress_path=""))
    assert "Vertragsmitteilungen" in html and "Haus 2" in html and "gesendet" in html
    with pytest.raises(FormError):
        _post(step="send", version_id=str(web.id), token=token)              # the preview is used once


def test_send_directly_from_the_form(db, web, smtp_server):  # noqa: F811
    _post(step="send", **_selection(db, web))
    assert len(smtp_server.outbox) == 2


def test_e_mail_date_must_be_today(db, web, smtp_server):  # noqa: F811
    with pytest.raises(FormError, match="heute"):
        _post(step="send", **_selection(db, web, day=date(2030, 1, 1)))
    assert smtp_server.outbox == []


def test_test_mail_goes_only_to_the_given_address_and_records_nothing(db, web, smtp_server, flashes):  # noqa: F811
    location = _post(step="preview", **_selection(db, web))
    token = location.split("&t=")[1]
    _post(step="test", version_id=str(web.id), token=token, to_address="pruefer@example.ch")
    (sent,) = smtp_server.outbox
    assert sent["to"] == ["pruefer@example.ch"] and sent["message"]["Subject"].startswith("[TEST] ")
    assert _attachments(sent["message"])[0][0] == "Gesellschaftsvertrag_v2.pdf"
    assert list_notices(db) == [] and all(a.notified_at is None for a in list_assignments_for_version(db, web.id))
    with pytest.raises(FormError):
        _post(step="test", version_id=str(web.id), token=token, to_address="kein-mail")


def test_draft_pdf_only_for_the_own_preview(db, web):  # noqa: F811
    location = _post(step="preview", **_selection(db, web, method="letter", day=date(2027, 1, 10)))
    token = location.split("&t=")[1]
    a2 = _ids(db, web)["p2"]
    name, data = checklist_page.notice_draft_pdf(db, {"t": token, "a": str(a2)})
    assert name == "Vertragsmitteilung_v2_Entwurf.pdf" and data.startswith(b"%PDF-")
    assert "Guten Tag Haus 2" in _FakeHTML.rendered[-1]
    with pytest.raises(FormError):
        checklist_page.notice_draft_pdf(db, {"t": "falsch", "a": str(a2)})
    with pytest.raises(FormError):
        checklist_page.notice_draft_pdf(db, {"t": token, "a": "999"})


def test_unknown_outcome_is_resolved_on_the_notice_page(db, web, monkeypatch, flashes):  # noqa: F811
    ids = _ids(db, web)
    _send(db, web, [ids["p2"]], _Transport(MailError("Verbindung abgebrochen", uncertain=True)))
    notice = list_notices(db)[0]
    html = documents_page.handle_get(RequestContext(method="GET", ingress_path="", segments=["notices", str(notice.id)]))
    assert "Ergebnis unklar" in html and f"/checklist/notice/{notice.id}/resolve" in html
    location = checklist_page.handle_post(RequestContext(method="POST", ingress_path="",
                                                         segments=["notice", str(notice.id), "resolve"],
                                                         form={"found": "0"}))
    assert location == f"/documents/notices/{notice.id}" and list_notices(db)[0].status == "failed"


# ── through the web server ──


def test_notice_pdf_routes_need_login(server, fake_pdf):  # noqa: F811
    from tests.test_auth import Browser, _setup_admin

    for path in ("/documents/notices/1/pdf", "/checklist/notice-pdf?t=x&a=1"):
        status, headers, body = Browser(server["port"]).get(path)
        assert status == 303 and "login" in headers["Location"] and not body.startswith("%PDF-")
    browser = _setup_admin(server)
    status, headers, _ = browser.get("/documents/notices/999/pdf")
    assert status == 303 and headers["Location"] == "/documents"                 # unknown: back with the reason
    status, headers, _ = browser.get("/checklist/notice-pdf?t=falsch&a=1")
    assert status == 303 and headers["Location"] == "/checklist"
    status, headers, _ = browser.post("/checklist/batch", {"kind": "notice", "step": "send", "csrf_token": "falsch"})
    assert status in (303, 400, 403) and list_notices(server["db"]) == []
