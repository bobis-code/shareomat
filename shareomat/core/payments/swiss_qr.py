# -*- coding: utf-8 -*-
"""
File: shareomat/core/payments/swiss_qr.py

Purpose:
    Swiss QR bill (QR-Rechnung): validated payload value object ("Swiss
    Payments Code"), references (QRR for a QR-IBAN, SCOR / ISO 11649
    otherwise) and the QR code as inline SVG with the Swiss cross —
    generated locally, never through an external service.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    SIX "Swiss Implementation Guidelines QR-bill" 2.3 (2.4 from the SIC
    release of 13.11.2026 has no technical difference for CHF):
      - QR type SPC, version 0200, coding 1 (UTF-8), lines separated by CR LF,
        trailer EPD; no alternative procedures, no /SWICO billing data.
      - Structured addresses only (type S); combined addresses (K) are no
        longer accepted.
      - CHF or EUR, amount 0.01 – 999 999 999.99 with two decimals.
      - QR-IBAN (institution id 30000–31999) <=> reference type QRR
        (27 digits, modulo 10 recursive). A normal IBAN takes SCOR
        (RF + 2 check digits + up to 21 characters, ISO 11649) or NON.
      - Error correction level M, code printed 46 x 46 mm, Swiss cross
        7 x 7 mm in the middle.
    Invalid input raises SwissQrError — nothing is silently "fixed", so a
    document is never issued with a payload that differs from its data.
"""

from __future__ import annotations

import re
import textwrap
from dataclasses import dataclass, field
from decimal import Decimal

import segno

QR_RENDER_VERSION = "spc-0200/six-ig-2.3/svg-1"
CURRENCIES = ("CHF", "EUR")
MAX_AMOUNT = Decimal("999999999.99")

# ISO 3166-1 alpha-2 (officially assigned codes).
ISO_COUNTRIES = frozenset("""AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW""".split())

# Character set of the guidelines (Latin subset): printable ASCII, Latin-1
# supplement and Latin Extended-A, plus Ș ș Ț ț and €.
_INVALID_CHARS = re.compile(r"[^\x20-\x7E -ſȘ-ț€]")


class SwissQrError(ValueError):
    """Data that can't go into a valid Swiss QR bill; the message names the field."""


def normalize_iban(raw: str) -> str:
    return re.sub(r"\s+", "", raw or "").upper()


def format_iban(iban: str) -> str:
    iban = normalize_iban(iban)
    return " ".join(iban[i:i + 4] for i in range(0, len(iban), 4))


def iban_is_valid(iban: str) -> bool:
    """CH/LI IBAN: 21 characters, 5-digit institution id, ISO 13616 mod-97 check."""
    iban = normalize_iban(iban)
    if not re.fullmatch(r"(CH|LI)\d{7}[A-Z0-9]{12}", iban):
        return False
    return int("".join(str(int(c, 36)) for c in iban[4:] + iban[:4])) % 97 == 1


def is_qr_iban(iban: str) -> bool:
    """A QR-IBAN has an institution id (positions 5–9) between 30000 and 31999."""
    iban = normalize_iban(iban)
    return iban_is_valid(iban) and 30000 <= int(iban[4:9]) <= 31999


def reference_type_for(iban: str) -> str:
    """The only reference type Shareomat uses for this account: QRR for a QR-IBAN, else SCOR."""
    return "QRR" if is_qr_iban(iban) else "SCOR"


def _mod10_recursive(digits: str) -> str:
    table = (0, 9, 4, 6, 8, 2, 7, 1, 3, 5)
    carry = 0
    for d in digits:
        carry = table[(carry + int(d)) % 10]
    return str((10 - carry) % 10)


def qr_reference(document_number: str) -> str:
    """27-digit QR reference (QRR) from a document number (letters mapped to 10..35)."""
    digits = "".join(c if c.isdigit() else str(int(c, 36)) for c in document_number.upper() if c.isalnum())
    if len(digits) > 26:
        raise SwissQrError(f"Belegnummer {document_number} ergibt keine QR-Referenz (zu lang).")
    body = digits.rjust(26, "0")
    return body + _mod10_recursive(body)


def creditor_reference(document_number: str) -> str:
    """ISO 11649 creditor reference (RFxx + document number without separators)."""
    ref = re.sub(r"[^A-Za-z0-9]", "", document_number).upper()
    if not 1 <= len(ref) <= 21:
        raise SwissQrError(f"Belegnummer {document_number} ergibt keine SCOR-Referenz.")
    check = 98 - int("".join(str(int(c, 36)) for c in ref + "RF00")) % 97
    return f"RF{check:02d}{ref}"


def reference_is_valid(reference_type: str, reference: str) -> bool:
    if reference_type == "QRR":
        return bool(re.fullmatch(r"\d{27}", reference)) and _mod10_recursive(reference[:26]) == reference[26]
    if reference_type == "SCOR":
        if not re.fullmatch(r"RF\d{2}[A-Z0-9]{1,21}", reference):
            return False
        rearranged = reference[4:] + reference[:4]
        return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1
    return reference_type == "NON" and reference == ""


def reference_for(iban: str, document_number: str) -> tuple[str, str]:
    """(type, reference) for a document — derived from its immutable number, so a reprint keeps it
    and a replacement document (new number) gets a new one."""
    if is_qr_iban(iban):
        return "QRR", qr_reference(document_number)
    return "SCOR", creditor_reference(document_number)


def format_reference(reference: str) -> str:
    """Display grouping: QRR 2 + groups of 5, SCOR groups of 4."""
    if reference.isdigit() and len(reference) == 27:
        return reference[:2] + " " + " ".join(reference[i:i + 5] for i in range(2, 27, 5))
    return " ".join(reference[i:i + 4] for i in range(0, len(reference), 4))


def check_text(label: str, value: str, limit: int, *, required: bool = False) -> str:
    """Validate one free-text field against the QR bill rules (no silent cleaning)."""
    value = (value or "").strip()
    if required and not value:
        raise SwissQrError(f"{label} fehlt.")
    if "\n" in value or "\r" in value:
        raise SwissQrError(f"{label}: Zeilenumbruch nicht erlaubt.")
    bad = sorted(set(_INVALID_CHARS.findall(value)))
    if bad:
        raise SwissQrError(f"{label}: unzulässige Zeichen {''.join(bad)!r}.")
    if len(value) > limit:
        raise SwissQrError(f"{label}: höchstens {limit} Zeichen.")
    return value


def split_street(line: str) -> tuple[str, str]:
    """'Hinterholzring 24' -> ('Hinterholzring', '24'); without a trailing number the whole line is the street."""
    m = re.fullmatch(r"(.*?\S)\s+(\d+\s?[A-Za-z]?(?:[-/]\d+[A-Za-z]?)?)", (line or "").strip())
    return (m.group(1), m.group(2).replace(" ", "")) if m else ((line or "").strip(), "")


@dataclass(frozen=True)
class QrAddress:
    """Structured address, type S."""

    name: str
    street: str = ""
    building: str = ""
    postal_code: str = ""
    city: str = ""
    country: str = "CH"

    def validated(self, role: str) -> "QrAddress":
        country = (self.country or "").strip().upper()
        if country not in ISO_COUNTRIES:
            raise SwissQrError(f"{role}: Land muss ein gültiger ISO-3166-Code sein (z. B. CH, LI).")
        return QrAddress(
            name=check_text(f"{role}: Name", self.name, 70, required=True),
            street=check_text(f"{role}: Strasse", self.street, 70),
            building=check_text(f"{role}: Hausnummer", self.building, 16),
            postal_code=check_text(f"{role}: PLZ", self.postal_code, 16, required=True),
            city=check_text(f"{role}: Ort", self.city, 35, required=True),
            country=country,
        )

    def lines(self) -> list[str]:
        return ["S", self.name, self.street, self.building, self.postal_code, self.city, self.country]


@dataclass(frozen=True)
class SwissQrPayload:
    """One Swiss QR bill — validated on construction via build()."""

    iban: str
    creditor: QrAddress
    amount: Decimal | None
    currency: str
    debtor: QrAddress | None
    reference_type: str
    reference: str
    message: str = ""

    @classmethod
    def build(cls, *, iban: str, creditor: QrAddress, amount: Decimal | None, debtor: QrAddress | None,
              reference_type: str, reference: str, message: str = "", currency: str = "CHF") -> "SwissQrPayload":
        iban = normalize_iban(iban)
        if not iban_is_valid(iban):
            raise SwissQrError("IBAN ungültig (CH/LI, 21 Zeichen, Prüfziffer).")
        if currency not in CURRENCIES:
            raise SwissQrError("Währung muss CHF oder EUR sein.")
        if is_qr_iban(iban) and currency != "CHF":
            raise SwissQrError("QR-IBAN und QR-Referenz sind nur in CHF zulässig.")
        if amount is not None:
            if amount != amount.quantize(Decimal("0.01")) or not Decimal("0.01") <= amount <= MAX_AMOUNT:
                raise SwissQrError("Betrag muss zwischen 0.01 und 999 999 999.99 liegen (zwei Dezimalstellen).")
        if is_qr_iban(iban) and reference_type != "QRR":
            raise SwissQrError("Eine QR-IBAN verlangt eine QR-Referenz (QRR).")
        if not is_qr_iban(iban) and reference_type == "QRR":
            raise SwissQrError("Eine QR-Referenz (QRR) ist nur mit einer QR-IBAN erlaubt.")
        if not reference_is_valid(reference_type, reference):
            raise SwissQrError(f"Referenz {reference!r} ist für Typ {reference_type} ungültig.")
        payload = cls(iban=iban, creditor=creditor.validated("Zahlungsempfänger"), amount=amount, currency=currency,
                      debtor=debtor.validated("Zahlungspflichtiger") if debtor is not None else None,
                      reference_type=reference_type, reference=reference,
                      message=check_text("Mitteilung", message, 140))
        problems = layout_problems(payload)
        if problems:
            raise SwissQrError(" ".join(problems))
        return payload

    def text(self) -> str:
        """The exact text encoded in the QR code."""
        lines = (["SPC", "0200", "1", self.iban] + self.creditor.lines() + [""] * 7
                 + [f"{self.amount:.2f}" if self.amount is not None else "", self.currency]
                 + (self.debtor.lines() if self.debtor is not None else [""] * 7)
                 + [self.reference_type, self.reference, self.message, "EPD"])
        return "\r\n".join(lines)


# Payment part geometry (SIX style guide) used for the fit check below. Character width is estimated
# conservatively (0.62 em, Arial/Helvetica); anything that may not fit is refused, never cut off.
_PT = 0.3528                    # mm per point
_CHAR_EM = 0.62
_RECEIPT = {"width": 52.0, "height": 56.0, "value_pt": 8, "line_pt": 9, "heading_pt": 6, "box": 20.0}
_PAYMENT = {"width": 87.0, "height": 85.0, "value_pt": 10, "line_pt": 11, "heading_pt": 8, "box": 25.0}


def _wrapped_lines(text: str, width_mm: float, value_pt: int) -> int:
    per_line = max(1, int(width_mm / (value_pt * _PT * _CHAR_EM)))
    return len(textwrap.wrap(text, per_line, break_long_words=True, break_on_hyphens=True)) or 1


def _section_height(geo: dict, lines: list[str], *, box: bool = False) -> float:
    height = geo["line_pt"] * _PT                                     # heading
    if box:
        return height + geo["box"]
    return height + sum(_wrapped_lines(t, geo["width"], geo["value_pt"]) for t in lines) * geo["line_pt"] * _PT


def _address_lines(a: QrAddress) -> list[str]:
    street = " ".join(x for x in (a.street, a.building) if x)
    return [a.name] + ([street] if street else []) + [f"{a.postal_code} {a.city}"]


def layout_problems(p: "SwissQrPayload") -> list[str]:
    """Why the payment part could not show all data legibly inside its fixed areas (empty = fits)."""
    gap = 9 * _PT
    account = [format_iban(p.iban)] + _address_lines(p.creditor)
    reference = [format_reference(p.reference)] if p.reference else []
    problems = []
    for name, geo, parts in (
        ("Empfangsschein", _RECEIPT, [account, reference]),
        ("Zahlteil", _PAYMENT, [account, reference, [p.message] if p.message else []]),
    ):
        height = sum(_section_height(geo, part) + gap for part in parts if part)
        height += _section_height(geo, _address_lines(p.debtor) if p.debtor else [], box=p.debtor is None)
        if height > geo["height"]:
            problems.append(f"{name}: Empfänger-, Referenz- oder Schuldnerangaben sind zu lang für die feste "
                            f"Fläche ({height:.0f} mm statt höchstens {geo['height']:.0f} mm) — Namen/Adressen kürzen.")
    return problems


def qr_svg(payload_text: str) -> str:
    """The Swiss QR code as SVG: error correction M, printed 46 x 46 mm, Swiss cross 7 x 7 mm."""
    qr = segno.make(payload_text, error="m", boost_error=False, micro=False, encoding="utf-8")
    matrix = qr.matrix
    n = len(matrix)
    path = []
    for y, row in enumerate(matrix):
        x = 0
        while x < n:
            if row[x]:
                start = x
                while x < n and row[x]:
                    x += 1
                path.append(f"M{start} {y}h{x - start}v1h{start - x}z")
            else:
                x += 1
    s = n * 7 / 46                       # 7 mm of 46 mm
    o = (n - s) / 2
    b = s * 0.5 / 7                      # white frame 0.5 mm
    arm_w, arm_l = s * 1.17 / 7, s * 3.89 / 7
    c = n / 2
    cross = (f'<rect x="{o:.3f}" y="{o:.3f}" width="{s:.3f}" height="{s:.3f}" fill="#fff"/>'
             f'<rect x="{o + b:.3f}" y="{o + b:.3f}" width="{s - 2 * b:.3f}" height="{s - 2 * b:.3f}" fill="#000"/>'
             f'<rect x="{c - arm_w / 2:.3f}" y="{c - arm_l / 2:.3f}" width="{arm_w:.3f}" height="{arm_l:.3f}" fill="#fff"/>'
             f'<rect x="{c - arm_l / 2:.3f}" y="{c - arm_w / 2:.3f}" width="{arm_l:.3f}" height="{arm_w:.3f}" fill="#fff"/>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" width="46mm" height="46mm" '
            f'shape-rendering="crispEdges" role="img" aria-label="Swiss QR Code">'
            f'<rect width="{n}" height="{n}" fill="#fff"/><path fill="#000" d="{"".join(path)}"/>{cross}</svg>')


@dataclass
class PaymentPartView:
    """What the payment part template shows — always derived from one SwissQrPayload."""

    payload: SwissQrPayload
    svg: str = field(init=False)
    iban_display: str = field(init=False)
    reference_display: str = field(init=False)

    def __post_init__(self) -> None:
        self.svg = qr_svg(self.payload.text())
        self.iban_display = format_iban(self.payload.iban)
        self.reference_display = format_reference(self.payload.reference)
