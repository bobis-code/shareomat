# -*- coding: utf-8 -*-
"""E-mail sending of documents: SMTP (core.delivery.mail), PDF (core.delivery.pdf), the send flow with its
delivery log (core.delivery.document_mail) and the pages (invoices, E-Mail-Vorlagen). Synthetic data only."""

from __future__ import annotations

import hashlib
import re
import smtplib
import socket
import ssl
import sys
import threading
import time
from datetime import timedelta
from decimal import Decimal

import pytest

from shareomat.config import SmtpConfig, _parse_smtp, smtp_problems
from shareomat.core.delivery import document_mail, mail, pdf
from shareomat.core.delivery.document_mail import DocumentMailError, preview_document_email, send_document_email
from shareomat.core.delivery.mail import Attachment, MailError, OutgoingMail, build_message, send_mail, valid_address
from shareomat.database.audit import list_events
from shareomat.database.invoicing import (
    InvoicingError,
    cancel_document,
    create_reminder,
    get_document,
    list_documents,
    record_delivery,
    record_payment,
)
from shareomat.database.email_templates import save_email_template
from shareomat.database.participants import update_participant
from shareomat.security.permissions import required_permission
from shareomat.web.pages import email_templates as templates_page
from shareomat.web.pages import invoices as invoices_page
from shareomat.web.pages.invoices import render_document
from shareomat.web.rendering import FormError, Redirect, RequestContext
from shareomat.web.state import get_state
from tests.test_auth import Browser, _setup_admin, server  # noqa: F401  (server = HTTP fixture)
from tests.test_invoicing import (  # noqa: F401  (fixtures)
    QR_SVG,
    ISSUED,
    LATER,
    _SIMPLE,
    _addr,
    _by_participant,
    _ready_profile,
    _released_run,
    db_path,
    issued,
    runtime,
)
from shareomat.database.invoicing import issue_documents

PASSWORD = "app-passwort-geheim-123"
SMTP_OK = SmtpConfig(enabled=True, host="smtp.example.ch", port=465, security="ssl", username="leg@example.ch",
                     password=PASSWORD, from_address="leg@example.ch", from_name="LEG Test")


# ── helpers / fakes ─────────────────────────────────────────────────────────


def _get(page, segments=(), query=None):
    return page.handle_get(RequestContext(method="GET", ingress_path="", segments=list(segments), query=query or {}))


def _post(page, segments, form):
    return page.handle_post(RequestContext(method="POST", ingress_path="", segments=list(segments),
                                           form={"csrf_token": get_state().csrf_token, **form}))


class FakeServer:
    """Stands in for smtplib.SMTP_SSL / smtplib.SMTP and records what would have been sent."""

    def __init__(self):
        self.outbox = []
        self.connections = []
        self.fail_connect = None
        self.fail_login = None
        self.fail_send = None          # exception, or callable(message, to_addrs) -> exception | None
        self.offers_starttls = True
        self.during_send = None        # callable run while "sending"

    def factory(self, host, port, timeout=None, context=None):
        server = self

        class _Connection:
            def __init__(self):
                self.host, self.port, self.timeout, self.context = host, port, timeout, context
                self.tls_context = None
                self.login_args = None
                server.connections.append(self)
                if server.fail_connect:
                    raise server.fail_connect

            def ehlo(self):
                return 250, b"ok"

            def has_extn(self, name):
                return name == "starttls" and server.offers_starttls

            def starttls(self, context=None):
                self.tls_context = context

            def login(self, user, password):
                if server.fail_login:
                    raise server.fail_login
                self.login_args = (user, password)

            def send_message(self, message, from_addr=None, to_addrs=None):
                if server.during_send:
                    server.during_send()
                failure = server.fail_send(message, to_addrs) if callable(server.fail_send) else server.fail_send
                if failure:
                    raise failure
                server.outbox.append({"message": message, "from": from_addr, "to": to_addrs})
                return {}

            def quit(self):
                pass

            def close(self):
                pass

        return _Connection()


class _FakeFatal(BaseException):
    pass


class _FakeURLFetcher:
    def __init__(self, **options):
        self.options = options

    def __call__(self, url):
        return self.fetch(url)


class _FakeHTML:
    """WeasyPrint stand-in: asks the URL fetcher for every src="..." like the real one would."""

    rendered: list[str] = []

    def __init__(self, string, base_url=None, url_fetcher=None):
        self.string, self.url_fetcher = string, url_fetcher

    def write_pdf(self):
        _FakeHTML.rendered.append(self.string)
        for url in re.findall(r'src="([^"]+)"', self.string):
            self.url_fetcher(url)
        return b"%PDF-1.7\n% fake\n" + hashlib.sha256(self.string.encode()).hexdigest().encode()


@pytest.fixture
def fake_pdf(monkeypatch):
    _FakeHTML.rendered = []
    monkeypatch.setattr(pdf, "_load_weasyprint", lambda: (_FakeHTML, _FakeURLFetcher, _FakeFatal))
    return _FakeHTML


@pytest.fixture
def smtp_server(monkeypatch):
    fake = FakeServer()
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", fake.factory)
    monkeypatch.setattr(mail.smtplib, "SMTP", fake.factory)
    return fake


@pytest.fixture
def mailbox(runtime, smtp_server, fake_pdf):
    runtime.smtp = SmtpConfig(**SMTP_OK.__dict__)
    return smtp_server


@pytest.fixture
def flashes(monkeypatch):
    seen = []
    monkeypatch.setattr(get_state(), "set_flash", lambda message, ok=True: seen.append((message, ok)))
    return seen


def _attachments(message):
    return [(part.get_filename(), part.get_content()) for part in message.iter_attachments()]


def _send_page(doc_id, target="document", **form):
    with pytest.raises(Redirect):
        _post(invoices_page, [str(doc_id), "email"], {"target": target, "confirm": "on", **form})


# ── configuration ───────────────────────────────────────────────────────────


def test_smtp_section_defaults_off_and_password_never_in_repr():
    assert _parse_smtp({}) == SmtpConfig() and not SmtpConfig().enabled
    parsed = _parse_smtp({"smtp": {"enabled": True, "host": " smtp.example.ch ", "port": "587",
                                   "security": "STARTTLS", "username": "u", "password": PASSWORD,
                                   "from_address": "leg@example.ch"}})
    assert (parsed.host, parsed.port, parsed.security) == ("smtp.example.ch", 587, "starttls")
    assert PASSWORD not in repr(parsed) and "password=set" in repr(parsed)
    assert smtp_problems(parsed) == []
    assert _parse_smtp({"smtp": {"port": "x"}}) == SmtpConfig()            # malformed: off, never a failed start
    assert _parse_smtp({"smtp": "kaputt"}) == SmtpConfig()


def test_smtp_problems_name_what_is_missing():
    assert "ausgeschaltet" in smtp_problems(SmtpConfig())[0]
    problems = smtp_problems(SmtpConfig(enabled=True, security="plain"))
    text = " ".join(problems)
    assert "ssl" in text and "Passwort" in text and "Absenderadresse" in text


def test_addon_options_are_written_to_the_runtime_config(tmp_path, monkeypatch):
    from tests.test_addon_runtime_config import _generate
    import importlib.util
    from pathlib import Path

    addon_dir = Path(__file__).resolve().parent.parent / "ha_addon"
    spec = importlib.util.spec_from_file_location("generate_runtime_config", addon_dir / "generate_runtime_config.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, file in (("OPTIONS_PATH", "options.json"), ("CONFIG_OUTPUT", "leg_config.yaml"), ("ENV_OUTPUT", "env"),
                       ("EMSOMAT_MANIFEST", "manifest.json")):
        monkeypatch.setattr(module, name, tmp_path / file)
    config = _generate(module, smtp={"enabled": True, "host": "smtp.example.ch", "port": 587, "security": "starttls",
                                     "username": "u", "password": PASSWORD, "from_address": "leg@example.ch"})
    assert config["smtp"]["security"] == "starttls" and config["smtp"]["password"] == PASSWORD
    assert _generate(module)["smtp"]["enabled"] is False                  # no section: off


# ── SMTP ────────────────────────────────────────────────────────────────────


def test_valid_address_is_strict():
    assert valid_address("erika.muster@example.ch")
    for bad in ("", "Erika <e@example.ch>", "e@example.ch\r\nBcc: x@example.ch", "e @example.ch", "e@example",
                "e@exämple.ch", "e@example.ch,x@example.ch", '"e"@example.ch'):
        assert not valid_address(bad), bad


def test_message_has_one_recipient_no_bcc_and_the_pdf():
    message = build_message(SMTP_OK, OutgoingMail(to="erika@example.ch", subject="Rechnung R-1 für Nov. – äöü",
                                                  body="Guten Tag\nZeile 2", attachments=(Attachment("Rechnung_R-1.pdf", b"%PDF-1.7 x"),)))
    assert message["To"] == "erika@example.ch" and message["Bcc"] is None and message["Cc"] is None
    assert message["From"] == "LEG Test <leg@example.ch>" and message["Message-ID"].endswith("@example.ch>")
    assert str(message["Subject"]) == "Rechnung R-1 für Nov. – äöü"
    assert _attachments(message) == [("Rechnung_R-1.pdf", b"%PDF-1.7 x")]
    assert b"\r\nBcc" not in message.as_bytes()


@pytest.mark.parametrize("change", [
    {"to": "erika@example.ch\r\nBcc: x@example.ch"},
    {"subject": "Rechnung\r\nBcc: x@example.ch"},
    {"subject": "  "},
])
def test_header_injection_and_empty_subject_are_refused(change):
    values = {"to": "erika@example.ch", "subject": "Rechnung", "body": "Text", **change}
    with pytest.raises(MailError):
        build_message(SMTP_OK, OutgoingMail(**values))
    with pytest.raises(MailError):
        build_message(SmtpConfig(**{**SMTP_OK.__dict__, "from_name": "LEG\nBcc: x@example.ch"}),
                      OutgoingMail(to="erika@example.ch", subject="R", body="T"))


def test_attachment_names_are_plain():
    with pytest.raises(MailError, match="Dateiname"):
        build_message(SMTP_OK, OutgoingMail(to="e@example.ch", subject="R", body="T",
                                            attachments=(Attachment("../../etc/passwd.pdf", b"x"),)))


def test_ssl_sends_with_certificate_and_hostname_check(smtp_server):
    sent = send_mail(SMTP_OK, OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    connection = smtp_server.connections[0]
    assert connection.context.verify_mode == ssl.CERT_REQUIRED and connection.context.check_hostname
    assert connection.login_args == ("leg@example.ch", PASSWORD)
    assert smtp_server.outbox[0]["to"] == ["erika@example.ch"] and smtp_server.outbox[0]["from"] == "leg@example.ch"
    assert sent.message_id == smtp_server.outbox[0]["message"]["Message-ID"]


def test_starttls_is_required_before_login(smtp_server):
    smtp = SmtpConfig(**{**SMTP_OK.__dict__, "security": "starttls", "port": 587})
    send_mail(smtp, OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    assert smtp_server.connections[0].tls_context.verify_mode == ssl.CERT_REQUIRED
    smtp_server.offers_starttls = False
    with pytest.raises(MailError, match="kein STARTTLS"):
        send_mail(smtp, OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    assert len(smtp_server.outbox) == 1


def test_errors_are_explained_and_never_show_the_password(smtp_server):
    smtp_server.fail_login = smtplib.SMTPAuthenticationError(535, b"5.7.8 Username and Password not accepted")
    with pytest.raises(MailError, match="App-Passwort") as err:
        send_mail(SMTP_OK, OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    assert not err.value.uncertain and PASSWORD not in str(err.value)
    smtp_server.fail_login = None
    smtp_server.fail_connect = ssl.SSLCertVerificationError("certificate verify failed")
    with pytest.raises(MailError, match="Zertifikat"):
        send_mail(SMTP_OK, OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    smtp_server.fail_connect = socket.timeout("timed out")
    with pytest.raises(MailError, match="antwortet nicht"):
        send_mail(SMTP_OK, OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    smtp_server.fail_connect = None
    smtp_server.fail_send = smtplib.SMTPRecipientsRefused({"erika@example.ch": (550, b"no such user")})
    with pytest.raises(MailError, match="Empfängeradresse") as err:
        send_mail(SMTP_OK, OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    assert not err.value.uncertain
    smtp_server.fail_send = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    with pytest.raises(MailError, match="unklar") as err:
        send_mail(SMTP_OK, OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    assert err.value.uncertain and smtp_server.outbox == []


def test_nothing_is_sent_when_switched_off(smtp_server):
    with pytest.raises(MailError, match="ausgeschaltet"):
        send_mail(SmtpConfig(), OutgoingMail(to="erika@example.ch", subject="Hallo", body="Text"))
    assert smtp_server.connections == []


# ── PDF ─────────────────────────────────────────────────────────────────────


def test_pdf_refuses_every_url_inside_a_page(fake_pdf):
    assert pdf.render_pdf("<p>Beleg</p>").startswith(b"%PDF-")
    for url in ("file:///data/shareomat.db", "http://192.0.2.1/x.png", "data:image/png;base64,AAAA"):
        with pytest.raises(pdf.PdfError, match="externe Datei"):
            pdf.render_pdf(f'<p>Beleg</p><img src="{url}">')


def test_pdf_unavailable_is_a_clear_message(monkeypatch):
    monkeypatch.setitem(sys.modules, "weasyprint", None)        # import fails like a missing package
    reason = pdf.pdf_unavailable_reason()
    assert "nicht verfügbar" in reason
    with pytest.raises(pdf.PdfError, match="nicht verfügbar"):
        pdf.render_pdf("<p>x</p>")


def test_real_weasyprint_renders_and_blocks_local_files(db_path, runtime, issued):
    if pdf.pdf_unavailable_reason():
        pytest.skip("WeasyPrint/Pango not installed here (the add-on image has them)")
    doc = _by_participant(db_path)["cons_a"]
    data = pdf.render_pdf(invoices_page.printable_page(doc, for_pdf=True))
    assert data.startswith(b"%PDF-") and len(data) > 1000
    with pytest.raises(pdf.PdfError, match="externe Datei"):
        pdf.render_pdf('<p>x</p><img src="file:///etc/hosts">')


# ── sending a document ──────────────────────────────────────────────────────


def test_preview_shows_recipient_text_and_attachment(db_path, runtime, issued, mailbox):
    doc = _by_participant(db_path)["cons_a"]
    html = _get(invoices_page, [str(doc.id), "email"], {"target": "document"})
    assert "a@example.ch" in html and f"Rechnung {doc.number}" in html and f"Rechnung_{doc.number}.pdf" in html
    assert "Jetzt per E-Mail senden" in html and "Guten Tag Consumer A" in html
    assert mailbox.outbox == []                                           # a preview sends nothing


def test_sending_logs_attempt_and_result_and_marks_the_document_sent(db_path, runtime, issued, mailbox, flashes):
    doc = _by_participant(db_path)["cons_a"]
    _send_page(doc.id)
    (sent,) = mailbox.outbox
    assert sent["to"] == ["a@example.ch"]
    (name, content), = _attachments(sent["message"])
    assert name == f"Rechnung_{doc.number}.pdf" and content.startswith(b"%PDF-")
    assert doc.snapshot_html in _FakeHTML.rendered[-1]                   # the frozen document, nothing re-rendered
    after = get_document(db_path, doc.id)
    assert [(d.status, d.sent_by, d.method) for d in after.deliveries] == [
        ("attempt", "shareomat", "email"), ("sent", "shareomat", "email")]
    assert after.sent_at is not None and hashlib.sha256(content).hexdigest() in after.deliveries[-1].detail
    assert sent["message"]["Message-ID"] in after.deliveries[-1].detail
    assert flashes[-1][1] and "gesendet" in flashes[-1][0]
    events = [e for e in list_events(db_path, entity="billing_document") if e.action.startswith("delivery_")]
    assert {e.action for e in events} == {"delivery_attempt", "delivery_sent"}
    assert all("von Shareomat gesendet" in e.detail for e in events)
    assert PASSWORD not in " ".join(e.detail for e in events)
    detail = _get(invoices_page, [str(doc.id)])
    assert "von Shareomat gesendet" in detail and "E-Mail-Versand begonnen" in detail


def test_confirmation_is_required(db_path, runtime, issued, mailbox):
    doc = _by_participant(db_path)["cons_a"]
    with pytest.raises(FormError, match="geprüft"):
        _post(invoices_page, [str(doc.id), "email"], {"target": "document"})
    assert mailbox.outbox == []


def test_second_send_needs_resend_and_never_makes_a_second_claim(db_path, runtime, issued, mailbox, flashes):
    doc = _by_participant(db_path)["cons_a"]
    _send_page(doc.id)
    _send_page(doc.id)
    assert len(mailbox.outbox) == 1 and not flashes[-1][1] and "bereits zugestellt" in flashes[-1][0]
    _send_page(doc.id, resend="on")
    assert len(mailbox.outbox) == 2
    assert len(list_documents(db_path, doc_type="invoice")) == 2              # still the same two invoices
    assert get_document(db_path, doc.id).amount_chf == doc.amount_chf


def test_refused_send_is_logged_as_failed(db_path, runtime, issued, mailbox, flashes):
    doc = _by_participant(db_path)["cons_a"]
    mailbox.fail_login = smtplib.SMTPAuthenticationError(535, b"not accepted")
    _send_page(doc.id)
    after = get_document(db_path, doc.id)
    assert [d.status for d in after.deliveries] == ["attempt", "failed"] and after.sent_at is None
    assert "Nicht gesendet" in flashes[-1][0] and PASSWORD not in flashes[-1][0] + after.deliveries[-1].detail
    assert after.unknown_email_outcome() is None                         # failed is a known outcome
    mailbox.fail_login = None
    _send_page(doc.id)                                                    # a failed one can simply be sent again
    assert get_document(db_path, doc.id).sent_at is not None


def test_broken_connection_leaves_the_outcome_unknown_until_checked(db_path, runtime, issued, mailbox, flashes):
    doc = _by_participant(db_path)["cons_a"]
    mailbox.fail_send = smtplib.SMTPServerDisconnected("closed")
    _send_page(doc.id)
    after = get_document(db_path, doc.id)
    assert [d.status for d in after.deliveries] == ["attempt"] and after.unknown_email_outcome() is not None
    assert "Ergebnis unklar" in flashes[-1][0] and "Gesendet" in flashes[-1][0]
    assert "delivery_uncertain" in {e.action for e in list_events(db_path, entity="billing_document")}
    assert "Ergebnis unklar" in _get(invoices_page, [str(doc.id)])
    mailbox.fail_send = None
    _send_page(doc.id)                                                    # without "Erneut senden": refused
    assert mailbox.outbox == [] and "Gesendet" in flashes[-1][0]
    record_delivery(db_path, doc.id, "email", detail="in Gesendet gefunden")    # the operator's check
    assert get_document(db_path, doc.id).unknown_email_outcome() is None


def test_crash_after_handover_is_shown_as_unknown(db_path, runtime, issued, mailbox):
    doc = _by_participant(db_path)["cons_a"]

    def crash(*_):
        raise RuntimeError("power loss")

    with pytest.raises(DocumentMailError, match="unklar") as err:
        send_document_email(db_path, SMTP_OK, doc.id, page_html=invoices_page._pdf_page, transport=crash)
    assert err.value.uncertain and get_document(db_path, doc.id).unknown_email_outcome() is not None


def test_double_click_sends_once(db_path, runtime, issued, mailbox):
    doc = _by_participant(db_path)["cons_a"]
    mailbox.during_send = lambda: time.sleep(0.2)
    errors = []

    def click():
        try:
            send_document_email(db_path, SMTP_OK, doc.id, page_html=invoices_page._pdf_page)
        except DocumentMailError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=click) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(mailbox.outbox) == 1 and len(errors) == 1 and "bereits zugestellt" in errors[0]


def test_without_email_address_nothing_can_be_sent(db_path, runtime, issued, mailbox):
    doc = _by_participant(db_path)["cons_b"]
    with pytest.raises(DocumentMailError, match="keine E-Mail-Adresse"):
        preview_document_email(db_path, doc.id)
    assert "Kann nicht gesendet werden" in _get(invoices_page, [str(doc.id), "email"], {"target": "document"})


def test_not_configured_shows_how_and_refuses(db_path, runtime, issued, fake_pdf, smtp_server):
    doc = _by_participant(db_path)["cons_a"]
    html = _get(invoices_page, [str(doc.id), "email"], {"target": "document"})
    assert "nicht eingerichtet" in html and "smtp" in html and "Jetzt per E-Mail senden" not in html
    with pytest.raises(FormError, match="ausgeschaltet"):
        _post(invoices_page, [str(doc.id), "email"], {"target": "document", "confirm": "on"})
    assert "nicht eingerichtet" in _get(invoices_page, ["dispatch"], {"period": str(issued)})
    assert smtp_server.connections == []


def test_reminder_by_email_and_refused_after_payment(db_path, runtime, issued, mailbox, flashes):
    doc = _by_participant(db_path)["cons_a"]
    _send_page(doc.id)
    reminder = create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=5))
    _send_page(doc.id, target=f"reminder:{reminder.id}")
    message = mailbox.outbox[-1]["message"]
    assert "Zahlungserinnerung" in str(message["Subject"]) and "Offener Betrag: CHF 0.10" in message.get_body().get_content()
    assert _attachments(message)[0][0] == f"Zahlungserinnerung_{doc.number}.pdf"
    assert get_document(db_path, doc.id).reminders[0].sent_at is not None
    second = create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=40))
    record_payment(db_path, doc.id, "0.04", ISSUED, reference="teil", today=LATER)
    with pytest.raises(DocumentMailError, match="offene Betrag geändert"):
        preview_document_email(db_path, doc.id, "reminder", second.id)


def test_result_is_logged_even_if_a_payment_arrives_while_sending(db_path, runtime, issued, mailbox):
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper")
    reminder = create_reminder(db_path, doc.id, render_document, today=doc.due_date + timedelta(days=5))
    mailbox.during_send = lambda: record_payment(db_path, doc.id, "0.10", ISSUED, reference="gleichzeitig", today=LATER)
    send_document_email(db_path, SMTP_OK, doc.id, "reminder", reminder.id, page_html=invoices_page._pdf_page)
    after = get_document(db_path, doc.id)
    assert after.reminders[0].sent_at is not None and after.unknown_email_outcome("reminder", reminder.id) is None


def test_cancellation_notice_by_email(db_path, runtime, issued, mailbox):
    doc = _by_participant(db_path)["cons_a"]
    _send_page(doc.id)
    cancelled = cancel_document(db_path, doc.id, "falscher Zählerstand", render_document, today=ISSUED)
    html = _get(invoices_page, [str(doc.id)])
    assert f"Stornobeleg {cancelled.cancel_number}" in html and "Vorschau und senden" in html
    _send_page(doc.id, target="cancellation")
    message = mailbox.outbox[-1]["message"]
    assert f"Stornobeleg {cancelled.cancel_number} zu {doc.number}" in str(message["Subject"])
    assert _attachments(message)[0][0] == f"Stornobeleg_{cancelled.cancel_number}.pdf"
    assert get_document(db_path, doc.id).cancellation_sent_at is not None
    with pytest.raises(DocumentMailError, match="nicht mehr versendet"):
        preview_document_email(db_path, doc.id, "document")


def test_tampered_document_is_never_sent(db_path, runtime, issued, mailbox):
    from shareomat.database.sqlite import connect

    doc = _by_participant(db_path)["cons_a"]
    with connect(db_path) as conn:
        with conn:
            conn.execute("UPDATE billing_documents SET snapshot_html = snapshot_html || 'x' WHERE id = ?", (doc.id,))
    with pytest.raises(DocumentMailError):
        send_document_email(db_path, SMTP_OK, doc.id, page_html=invoices_page._pdf_page)
    assert mailbox.outbox == []


# ── Sammelversand ───────────────────────────────────────────────────────────


@pytest.fixture
def two_with_email(db_path, runtime):
    update_participant(db_path, "solar", _addr("solar", "Solar PV", email="solar@example.ch"))
    run_id = _released_run(db_path, runtime, _SIMPLE)
    _ready_profile(db_path)
    issue_documents(db_path, run_id, render_document, today=ISSUED)
    return run_id


def test_dispatch_sends_the_ticked_documents(db_path, runtime, two_with_email, mailbox, flashes):
    page = _get(invoices_page, ["dispatch"], {"period": str(two_with_email)})
    assert page.count('name="mail_') == 2 and page.count('name="pick_') == 3
    docs = _by_participant(db_path)
    picks = {f"mail_d{docs['cons_a'].id}": "on", f"mail_d{docs['solar'].id}": "on"}
    with pytest.raises(FormError, match="geprüft"):
        _post(invoices_page, ["dispatch", "email"], {"run_id": str(two_with_email), **picks})
    with pytest.raises(Redirect):
        _post(invoices_page, ["dispatch", "email"], {"run_id": str(two_with_email), "confirm": "on", **picks})
    assert sorted(m["to"][0] for m in mailbox.outbox) == ["a@example.ch", "solar@example.ch"]
    assert "2 E-Mail(s) gesendet" in flashes[-1][0] and flashes[-1][1]
    names = {_attachments(m["message"])[0][0] for m in mailbox.outbox}
    assert names == {f"Rechnung_{docs['cons_a'].number}.pdf", f"Gutschrift_{docs['solar'].number}.pdf"}
    after = _get(invoices_page, ["dispatch"], {"period": str(two_with_email)})
    assert after.count('name="mail_') == 0 and after.count('name="pick_') == 1        # only the paper one is left


def test_dispatch_stops_at_the_first_failure(db_path, runtime, two_with_email, mailbox, flashes):
    docs = _by_participant(db_path)
    first, second = sorted((docs["cons_a"], docs["solar"]), key=lambda d: f"d{d.id}")
    mailbox.fail_send = lambda message, to: (smtplib.SMTPDataError(552, b"too big")
                                             if to == [first.recipient_email] else None)
    with pytest.raises(Redirect):
        _post(invoices_page, ["dispatch", "email"], {"run_id": str(two_with_email), "confirm": "on",
                                                     f"mail_d{first.id}": "on", f"mail_d{second.id}": "on"})
    message, ok = flashes[-1]
    assert not ok and "Nicht gesendet" in message and "1 weitere" in message
    assert mailbox.outbox == [] and get_document(db_path, second.id).deliveries == []


# ── PDF links, test e-mail, permissions ─────────────────────────────────────


def test_pdf_download_is_the_attachment(db_path, runtime, issued, mailbox):
    doc = _by_participant(db_path)["cons_a"]
    name, data = invoices_page.pdf_download(db_path, [str(doc.id), "pdf"], {"target": "document"})
    assert name == f"Rechnung_{doc.number}.pdf" and data.startswith(b"%PDF-")
    assert "window.print()" not in _FakeHTML.rendered[-1]                # no print button in the PDF
    sample_name, sample = invoices_page.pdf_download(db_path, ["sample", "pdf"], {})
    assert sample_name == "Muster_PDF-Test.pdf" and "keine Rechnung" in _FakeHTML.rendered[-1]
    with pytest.raises(FormError):
        invoices_page.pdf_download(db_path, [str(doc.id), "pdf"], {"target": "reminder:x"})


def _test_post(**form):
    return templates_page.handle_post(RequestContext(method="POST", ingress_path="", segments=["test"],
                                                     form={"csrf_token": get_state().csrf_token, **form}))


@pytest.fixture(autouse=True)
def _fresh_test_limit():
    templates_page._test_sent.clear()
    yield
    templates_page._test_sent.clear()


def test_overview_offers_a_test_address_and_what_to_send(db_path, runtime, issued, mailbox):
    html = templates_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[], query={}))
    assert 'name="to_address"' in html and 'value="leg@example.ch"' in html and PASSWORD not in html
    assert "Muster-Rechnung (Max Muster, wie echt)" in html and "Muster-Gutschrift" in html
    assert "/invoices/sample/pdf?kind=invoice" in html


def test_sample_invoice_is_sent_like_a_real_one_but_marked_as_test(db_path, runtime, issued, mailbox, flashes):
    documents_before = len(list_documents(db_path))
    save_email_template(db_path, "invoice", "{gemeinschaft}: Ihre Rechnung {belegnummer}", "Hallo {name}, {betrag}.")
    _test_post(to_address="pruefer@example.ch", what="invoice")
    (sent,) = mailbox.outbox
    message = sent["message"]
    assert sent["to"] == ["pruefer@example.ch"]
    assert str(message["Subject"]) == "[TEST] LEG Test: Ihre Rechnung R-MUSTER"          # the stored template
    body = message.get_body().get_content()
    assert body.startswith("TEST – Muster für Max Muster") and "Hallo Max Muster, CHF 23.45." in body
    (name, content), = _attachments(message)
    assert name == "Rechnung_R-MUSTER.pdf" and content.startswith(b"%PDF-")
    page = _FakeHTML.rendered[-1]
    assert "MUSTER – KEINE RECHNUNG, NICHT BEZAHLEN" in page and "Musterweg 24" in page and "9999 Musterdorf" in page
    assert "Rechnung R-MUSTER" in page and QR_SVG in page                    # real layout with the QR payment part
    assert len(list_documents(db_path)) == documents_before                  # nothing stored as a document
    assert flashes[-1][1] and "pruefer@example.ch" in flashes[-1][0]
    (event,) = list_events(db_path, entity="email")
    assert event.action == "sent" and "Muster-Rechnung" in event.detail and "pruefer@example.ch" in event.detail


def test_sample_qr_says_not_to_pay(db_path, runtime, issued):
    from shareomat.database.invoicing import SAMPLE_QR_MESSAGE, sample_document
    doc = sample_document(db_path, "invoice", render_document, today=ISSUED)
    assert SAMPLE_QR_MESSAGE in doc.qr_payload and doc.number == "R-MUSTER" and doc.recipient_label == "Max Muster"
    assert (doc.period_start, doc.period_end) == (ISSUED.replace(month=7, day=1), ISSUED.replace(month=7, day=31))
    credit = sample_document(db_path, "credit", render_document, today=ISSUED)
    assert credit.number == "G-MUSTER" and not credit.qr_payload and "Gutschrift G-MUSTER" in credit.snapshot_html


def test_sample_credit_and_connection_test(db_path, runtime, issued, mailbox):
    _test_post(to_address="pruefer@example.ch", what="credit")
    _test_post(to_address="pruefer@example.ch", what="connection")
    names = [_attachments(m["message"])[0][0] for m in mailbox.outbox]
    assert names == ["Gutschrift_G-MUSTER.pdf", "Muster_PDF-Test.pdf"]
    assert str(mailbox.outbox[0]["message"]["Subject"]).startswith("[TEST] LEG Test: Gutschrift G-MUSTER")


@pytest.mark.parametrize("form, message", [
    ({"to_address": "kein-at-zeichen", "what": "invoice"}, "gültige E-Mail-Adresse"),
    ({"to_address": "a@example.ch\r\nBcc: x@example.ch", "what": "invoice"}, "gültige E-Mail-Adresse"),
    ({"to_address": "a@example.ch", "what": "alles"}, "wählen"),
])
def test_test_form_is_checked(db_path, runtime, issued, mailbox, form, message):
    with pytest.raises(FormError, match=message):
        _test_post(**form)
    assert mailbox.outbox == []


def test_test_mails_are_limited_per_hour(db_path, runtime, issued, mailbox, monkeypatch):
    monkeypatch.setattr(templates_page, "_TEST_MAILS_PER_HOUR", 2)
    _test_post(to_address="pruefer@example.ch", what="connection")
    _test_post(to_address="pruefer@example.ch", what="connection")
    with pytest.raises(FormError, match="pro Stunde"):
        _test_post(to_address="pruefer@example.ch", what="connection")
    assert len(mailbox.outbox) == 2


def test_failed_test_mail_is_logged(db_path, runtime, issued, mailbox, flashes):
    mailbox.fail_login = smtplib.SMTPAuthenticationError(535, b"no")
    _test_post(to_address="pruefer@example.ch", what="invoice")
    assert not flashes[-1][1] and "App-Passwort" in flashes[-1][0]
    assert [e.action for e in list_events(db_path, entity="email")] == ["failed"]


def test_sample_pdfs_can_be_viewed_before_sending(db_path, runtime, issued, fake_pdf):
    name, data = invoices_page.pdf_download(db_path, ["sample", "pdf"], {"kind": "invoice"})
    assert name == "Rechnung_R-MUSTER.pdf" and data.startswith(b"%PDF-") and "MUSTER" in _FakeHTML.rendered[-1]
    assert invoices_page.pdf_download(db_path, ["sample", "pdf"], {"kind": "credit"})[0] == "Gutschrift_G-MUSTER.pdf"


def test_overview_explains_setup_when_not_configured(db_path, runtime, fake_pdf):
    html = templates_page.handle_get(RequestContext(method="GET", ingress_path="", segments=[], query={}))
    assert "Noch nicht eingerichtet" in html and "App-Passwort" in html and "Test-E-Mail senden</button>" not in html


def test_permissions():
    assert required_permission("POST", "invoices", ["7", "email"]) == "invoice.manage"
    assert required_permission("POST", "invoices", ["dispatch", "email"]) == "invoice.manage"
    assert required_permission("GET", "invoices", ["7", "pdf"]) == "view"
    assert required_permission("POST", "email_templates", ["test"]) == "masterdata.edit"


def test_manual_delivery_log_is_unchanged(db_path, runtime, issued):
    doc = _by_participant(db_path)["cons_a"]
    record_delivery(db_path, doc.id, "paper", detail="Briefkasten")
    (entry,) = get_document(db_path, doc.id).deliveries
    assert (entry.status, entry.sent_by) == ("sent", "manual")
    with pytest.raises(InvoicingError, match="Zustellstatus"):
        record_delivery(db_path, doc.id, "email", status="attempt")      # attempts are Shareomat's own
    assert any("manuell erfasst" in e.detail for e in list_events(db_path, entity="billing_document"))


def test_document_amounts_in_mail_text(db_path, runtime, two_with_email, mailbox):
    doc = _by_participant(db_path)["solar"]
    preview = preview_document_email(db_path, doc.id)
    assert preview.subject.startswith("LEG Test: Gutschrift") and f"Betrag: CHF {doc.amount_chf:.2f}" in preview.body
    assert doc.amount_chf == Decimal("0.20")


def test_pdf_route_needs_login_and_returns_a_pdf(server, fake_pdf):  # noqa: F811
    status, headers, body = Browser(server["port"]).get("/invoices/sample/pdf")
    assert status == 303 and "login" in headers["Location"] and not body.startswith("%PDF-")
    browser = _setup_admin(server)
    status, headers, body = browser.get("/invoices/sample/pdf")
    assert status == 200 and headers["Content-Type"] == "application/pdf" and body.startswith("%PDF-")
    assert headers["Content-Disposition"] == 'inline; filename="Muster_PDF-Test.pdf"'
    assert headers["Cache-Control"] == "no-store"
    status, headers, _ = browser.get("/invoices/999/pdf?target=document")
    assert status == 303 and headers["Location"] == "/invoices/999"
