# -*- coding: utf-8 -*-
"""
File: shareomat/core/email_templates.py

Purpose:
    The e-mail templates Shareomat knows (Willkommen, Rechnung, Gutschrift,
    Stornobeleg, Zahlungserinnerung/Mahnung), their default texts and
    placeholders, and the one function that fills a template. The admin
    page "E-Mail-Vorlagen" edits them; sending
    (shareomat.core.delivery.document_mail) uses exactly the same
    fill_template(), so what the preview shows is what is sent.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    Templates are plain text with {platzhalter} placeholders — deliberately
    NOT Jinja: an operator-edited text must never be able to execute
    template code (server-side template injection). Only the placeholders
    listed for a kind are allowed; anything else is refused on save.
    Values never contain line breaks, and a filled subject never does either,
    so a later SMTP header can not be split (header injection).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from shareomat.models.invoicing import BillingDocument, DocumentReminder

MAX_SUBJECT_CHARS = 200
MAX_BODY_CHARS = 10_000

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
_LINE_BREAKS = re.compile(r"[\r\n  \x0b\x0c\x85]+")
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class TemplateSpec:
    """What a template kind is for, what it is sent with, and which placeholders it may use."""

    kind: str
    label: str
    purpose: str
    attachment: str
    placeholders: tuple[tuple[str, str], ...]   # (name, description)
    default_subject: str
    default_body: str


_COMMON = (
    ("name", "Name des Teilnehmers"),
    ("gemeinschaft", "Name der Gemeinschaft (LEG)"),
    ("vertreter_name", "Name des LEG-Vertreters"),
    ("vertreter_email", "E-Mail des LEG-Vertreters"),
)

_DOCUMENT = (
    ("belegnummer", "Nummer des Belegs"),
    ("belegdatum", "Ausstellungsdatum"),
    ("zeitraum", "Abrechnungszeitraum"),
    ("betrag", "Betrag, z. B. CHF 12.30"),
    ("energie_kwh", "LEG-Strom des Zeitraums in kWh"),
)

_SIGNATURE = """Freundliche Grüsse
{vertreter_name}
{gemeinschaft}"""

_WELCOME_BODY = """Guten Tag {name}

Herzlich willkommen in der {gemeinschaft}! Wir freuen uns, dass Sie dabei sind.

Im Anhang finden Sie den Gesellschaftsvertrag (Version {vertrag_version}, gültig ab {vertrag_gueltig_ab}) und Ihre Beitrittserklärung. Ihr Beitritt ist per {beitritt_per} vorgesehen.

Bitte senden Sie uns die Beitrittserklärung unterschrieben zurück. LEG-Strom wird ab dem Beginn zugeteilt, den der Netzbetreiber bestätigt; wir informieren Sie darüber.

Bei Fragen erreichen Sie uns unter {vertreter_email}.

""" + _SIGNATURE

_INVOICE_BODY = """Guten Tag {name}

Im Anhang finden Sie die Rechnung {belegnummer} vom {belegdatum} für Ihren LEG-Strombezug im Zeitraum {zeitraum}.

LEG-Strom: {energie_kwh} kWh
Betrag: {betrag}
Zahlbar bis: {faellig_am}

Die Zahlungsangaben stehen auf der Rechnung. Bei Fragen erreichen Sie uns unter {vertreter_email}.

""" + _SIGNATURE

_CREDIT_BODY = """Guten Tag {name}

Im Anhang finden Sie die Gutschrift {belegnummer} vom {belegdatum} für Ihre LEG-Stromlieferung im Zeitraum {zeitraum}.

LEG-Strom: {energie_kwh} kWh
Betrag: {betrag}

Die Auszahlung erfolgt, sobald die Zahlungen der LEG-Bezüger für diesen Zeitraum eingegangen sind (Gesellschaftsvertrag Ziff. 3.4). Bei Fragen erreichen Sie uns unter {vertreter_email}.

""" + _SIGNATURE

_CANCELLATION_BODY = """Guten Tag {name}

Der Beleg {belegart} {belegnummer} vom {belegdatum} für den Zeitraum {zeitraum} wurde storniert und ist damit ungültig. Im Anhang finden Sie den Stornobeleg {stornonummer}.

Ein allfälliger Ersatzbeleg folgt separat. Bei Fragen erreichen Sie uns unter {vertreter_email}.

""" + _SIGNATURE

_REMINDER_BODY = """Guten Tag {name}

Im Anhang finden Sie eine {mahnstufe} zur Rechnung {belegnummer} vom {belegdatum} für den Zeitraum {zeitraum}.

Offener Betrag: {offener_betrag}
Bitte bezahlen bis: {neue_frist}

Die Zahlungsangaben stehen im Anhang. Falls Sie den Betrag in den letzten Tagen bereits bezahlt haben, betrachten Sie dieses Schreiben bitte als gegenstandslos. Bei Fragen erreichen Sie uns unter {vertreter_email}.

""" + _SIGNATURE

_CONTRACT_NOTICE_BODY = """Guten Tag {name}

Der Gesellschaftsvertrag der {gemeinschaft} gilt ab {vertrag_gueltig_ab} in einer neuen Fassung (Version {vertrag_version}). Im Anhang finden Sie den vollständigen Vertrag.

Bei Fragen erreichen Sie uns unter {vertreter_email}.

""" + _SIGNATURE

TEMPLATE_SPECS: dict[str, TemplateSpec] = {
    spec.kind: spec for spec in (
        TemplateSpec(
            kind="welcome",
            label="Willkommen",
            purpose="Für neue Teilnehmer beim Beitritt.",
            attachment="Gesellschaftsvertrag und Beitrittserklärung (PDF)",
            placeholders=_COMMON + (
                ("vertrag_version", "Nummer der Vertragsversion"),
                ("vertrag_gueltig_ab", "Vertragsversion gültig ab"),
                ("beitritt_per", "Beitrittsdatum"),
            ),
            default_subject="Willkommen in der {gemeinschaft}",
            default_body=_WELCOME_BODY,
        ),
        TemplateSpec(
            kind="contract_notice",
            label="Vertragsmitteilung",
            purpose="Für Teilnehmer, wenn eine neue Version des Gesellschaftsvertrags mitgeteilt wird "
                    "(Checkliste → Mitteilungen). Als Brief steht derselbe Text über dem Vertrag.",
            attachment="Gesellschaftsvertrag der neuen Version (PDF)",
            placeholders=_COMMON + (
                ("vertrag_version", "Nummer der neuen Vertragsversion"),
                ("vertrag_gueltig_ab", "Neue Version gültig ab"),
            ),
            default_subject="{gemeinschaft}: Gesellschaftsvertrag Version {vertrag_version}, gültig ab {vertrag_gueltig_ab}",
            default_body=_CONTRACT_NOTICE_BODY,
        ),
        TemplateSpec(
            kind="invoice",
            label="Abrechnung: Rechnung",
            purpose="Für LEG-Bezüger, wenn eine Rechnung versendet wird.",
            attachment="Rechnung (PDF)",
            placeholders=_COMMON + _DOCUMENT + (("faellig_am", "Zahlbar bis"),),
            default_subject="{gemeinschaft}: Rechnung {belegnummer} für {zeitraum}",
            default_body=_INVOICE_BODY,
        ),
        TemplateSpec(
            kind="credit",
            label="Abrechnung: Gutschrift",
            purpose="Für LEG-Produzenten, wenn eine Gutschrift versendet wird.",
            attachment="Gutschrift (PDF)",
            placeholders=_COMMON + _DOCUMENT,
            default_subject="{gemeinschaft}: Gutschrift {belegnummer} für {zeitraum}",
            default_body=_CREDIT_BODY,
        ),
        TemplateSpec(
            kind="cancellation",
            label="Abrechnung: Stornobeleg",
            purpose="Wenn ein bereits zugestellter Beleg storniert wurde.",
            attachment="Stornobeleg (PDF)",
            placeholders=_COMMON + (
                ("stornonummer", "Nummer des Stornobelegs"),
                ("belegart", "Rechnung oder Gutschrift"),
                ("belegnummer", "Nummer des stornierten Belegs"),
                ("belegdatum", "Datum des stornierten Belegs"),
                ("zeitraum", "Abrechnungszeitraum"),
            ),
            default_subject="{gemeinschaft}: Stornobeleg {stornonummer} zu {belegnummer}",
            default_body=_CANCELLATION_BODY,
        ),
        TemplateSpec(
            kind="reminder",
            label="Abrechnung: Zahlungserinnerung / Mahnung",
            purpose="Für eine überfällige Rechnung, je nach Stufe Zahlungserinnerung, 1. oder 2. Mahnung.",
            attachment="Zahlungserinnerung bzw. Mahnung mit QR-Zahlteil (PDF)",
            placeholders=_COMMON + (
                ("mahnstufe", "Zahlungserinnerung, 1. Mahnung oder 2. Mahnung"),
                ("belegnummer", "Nummer der Rechnung"),
                ("belegdatum", "Datum der Rechnung"),
                ("zeitraum", "Abrechnungszeitraum"),
                ("offener_betrag", "Offener Betrag, z. B. CHF 12.30"),
                ("neue_frist", "Neue Zahlungsfrist"),
            ),
            default_subject="{gemeinschaft}: {mahnstufe} zur Rechnung {belegnummer}",
            default_body=_REMINDER_BODY,
        ),
    )
}


class TemplateError(ValueError):
    """A template text that can not be stored (unknown placeholder, too long, line break in the subject)."""


# ── Private helpers ─────────────────────────────────────────────────────────


def _date(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else "–"


def _clean_value(value: object) -> str:
    """One line of plain text: no line breaks, no control characters."""
    text = _LINE_BREAKS.sub(" ", str(value))
    return _CONTROL_CHARS.sub("", text).strip()


def _format_kwh(value: Decimal) -> str:
    return f"{Decimal(value).quantize(Decimal('0.1')):,}".replace(",", "'")


def _format_chf(value: Decimal) -> str:
    return "CHF " + f"{Decimal(value).quantize(Decimal('0.01')):,}".replace(",", "'")


# ── Public functions ────────────────────────────────────────────────────────


def spec_for(kind: str) -> TemplateSpec:
    """The spec of a known kind; KeyError for anything else (callers check with `kind in TEMPLATE_SPECS`)."""
    return TEMPLATE_SPECS[kind]


def placeholders_in(text: str) -> list[str]:
    """Placeholder names used in a text, in order of first appearance."""
    seen: list[str] = []
    for name in _PLACEHOLDER.findall(text):
        if name not in seen:
            seen.append(name)
    return seen


def validate_template(kind: str, subject: str, body: str) -> tuple[str, str]:
    """Return (subject, body) normalised for storage, or raise TemplateError with a message for the operator."""
    spec = spec_for(kind)
    subject = subject.strip()
    body = body.replace("\r\n", "\n").replace("\r", "\n").strip("\n")
    if not subject:
        raise TemplateError("Der Betreff darf nicht leer sein.")
    if not body.strip():
        raise TemplateError("Der Text darf nicht leer sein.")
    if _LINE_BREAKS.search(subject):
        raise TemplateError("Der Betreff muss einzeilig sein.")
    if len(subject) > MAX_SUBJECT_CHARS:
        raise TemplateError(f"Der Betreff ist zu lang (höchstens {MAX_SUBJECT_CHARS} Zeichen).")
    if len(body) > MAX_BODY_CHARS:
        raise TemplateError(f"Der Text ist zu lang (höchstens {MAX_BODY_CHARS} Zeichen).")
    allowed = {name for name, _ in spec.placeholders}
    unknown = [name for name in placeholders_in(subject + "\n" + body) if name not in allowed]
    if unknown:
        listed = ", ".join("{" + name + "}" for name in unknown)
        raise TemplateError(f"Unbekannter Platzhalter: {listed}. Erlaubt sind nur die aufgeführten Platzhalter.")
    return subject, body


def fill_template(subject: str, body: str, values: dict[str, object]) -> tuple[str, str]:
    """Replace {platzhalter} by its value; a placeholder without a value stays visible as is.

    The result is plain text. The subject is always a single line.
    """
    cleaned = {name: _clean_value(value) for name, value in values.items()}

    def replace(match: re.Match) -> str:
        return cleaned.get(match.group(1), match.group(0))

    filled_subject = _clean_value(_PLACEHOLDER.sub(replace, subject))
    filled_body = _PLACEHOLDER.sub(replace, body)
    return filled_subject, filled_body


def common_values(community_name: str, representative_name: str, representative_email: str,
                  participant_label: str) -> dict[str, str]:
    """Values every template kind has."""
    return {
        "name": participant_label,
        "gemeinschaft": community_name,
        "vertreter_name": representative_name,
        "vertreter_email": representative_email,
    }


def welcome_values(common: dict[str, str], *, contract_version: int | None, contract_valid_from: date | None,
                   joined_at: date | None) -> dict[str, str]:
    """Values of the "welcome" template for one membership."""
    return {
        **common,
        "vertrag_version": str(contract_version) if contract_version is not None else "–",
        "vertrag_gueltig_ab": _date(contract_valid_from),
        "beitritt_per": _date(joined_at),
    }


def contract_notice_values(common: dict[str, str], *, contract_version: int,
                           contract_valid_from: date | None) -> dict[str, str]:
    """Values of the "contract_notice" template for one new contract version."""
    return {
        **common,
        "vertrag_version": str(contract_version),
        "vertrag_gueltig_ab": _date(contract_valid_from),
    }


def _period(doc: BillingDocument) -> str:
    if doc.period_start and doc.period_end:
        return f"{_date(doc.period_start)} – {_date(doc.period_end)}"
    return "–"


def document_values(common: dict[str, str], doc: BillingDocument) -> dict[str, str]:
    """Values of the "invoice"/"credit" template for one issued document."""
    values = {
        **common,
        "belegnummer": doc.number,
        "belegdatum": _date(doc.issue_date),
        "zeitraum": _period(doc),
        "betrag": _format_chf(doc.amount_chf),
        "energie_kwh": _format_kwh(doc.local_kwh),
    }
    if doc.is_invoice:
        values["faellig_am"] = _date(doc.due_date)
    return values


def cancellation_values(common: dict[str, str], doc: BillingDocument) -> dict[str, str]:
    """Values of the "cancellation" template for the Stornobeleg of a cancelled document."""
    return {
        **common,
        "stornonummer": doc.cancel_number or "–",
        "belegart": "Rechnung" if doc.is_invoice else "Gutschrift",
        "belegnummer": doc.number,
        "belegdatum": _date(doc.issue_date),
        "zeitraum": _period(doc),
    }


def reminder_values(common: dict[str, str], doc: BillingDocument, reminder: DocumentReminder) -> dict[str, str]:
    """Values of the "reminder" template for one reminder of an invoice."""
    return {
        **common,
        "mahnstufe": reminder.label,
        "belegnummer": doc.number,
        "belegdatum": _date(doc.issue_date),
        "zeitraum": _period(doc),
        "offener_betrag": _format_chf(reminder.open_amount_chf),
        "neue_frist": _date(reminder.new_due_date),
    }
