# -*- coding: utf-8 -*-
"""
File: shareomat/core/delivery/pdf.py

Purpose:
    Turn a printable document page (the same HTML the "Drucken" page
    shows: frozen document body + SHA-256 footer) into a PDF, with
    WeasyPrint. Used for e-mail attachments and the "PDF" links.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    - Nothing is fetched while rendering. Every URL a page refers to
      (http, https, file, data, ...) stops the rendering with an error.
      Shareomat's documents are self-contained (inline styles, the QR code
      as inline SVG), so a URL inside one means the HTML is not what
      Shareomat produced. No local file can end up in a PDF and no request
      leaves the add-on (WeasyPrint docs, "Security": use a URL fetcher
      that refuses file:// and network access for untrusted HTML).
    - Fonts: the add-on image installs only Liberation Sans
      (font-liberation). SIX "Swiss Implementation Guidelines QR-bill" 2.3,
      §3.4: only Arial, Frutiger, Helvetica and Liberation Sans are
      permitted. Assumption: fontconfig maps "Arial" / "Helvetica" /
      "sans-serif" to Liberation Sans, the only font installed.
      Needs verification: the test PDF on the real add-on.
    - WeasyPrint and its Pango libraries are imported only when a PDF is
      needed. Without them Shareomat runs as before; only PDF and e-mail
      are unavailable, with a clear message.
    - One rendering at a time (module lock); a PDF larger than
      MAX_PDF_BYTES is refused (a document page is a few 10 kB).
"""

from __future__ import annotations

import logging
import threading

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_HTML_CHARS = 2 * 1024 * 1024

_render_lock = threading.Lock()


class PdfError(RuntimeError):
    """A PDF can not be produced; the message is meant for the operator (German)."""


# ── Private helpers ─────────────────────────────────────────────────────────


def _load_weasyprint():
    """(HTML, URLFetcher, FatalURLFetchingError) or PdfError when WeasyPrint/Pango is missing."""
    try:
        import weasyprint
        from weasyprint.urls import FatalURLFetchingError, URLFetcher
    except (ImportError, OSError) as exc:
        raise PdfError(
            "PDF-Erzeugung ist in dieser Installation nicht verfügbar (WeasyPrint oder die "
            f"Pango-Bibliotheken fehlen: {type(exc).__name__}). Im Home-Assistant-Add-on ist sie "
            "enthalten; bei einer eigenen Installation siehe README."
        ) from exc
    # WeasyPrint warns about every CSS property it does not support; those are
    # screen-only rules of the shared print page, not errors of the document.
    logging.getLogger("weasyprint").setLevel(logging.ERROR)
    return weasyprint.HTML, URLFetcher, FatalURLFetchingError


def refused_url_message(url: str) -> str:
    """Why a URL inside a document stops the PDF (shown to the operator, URL shortened)."""
    shown = url if len(url) <= 80 else url[:77] + "..."
    return f"Der Beleg verweist auf eine externe Datei ({shown}) — PDF nicht erzeugt."


def _blocking_fetcher(url_fetcher_class, fatal_error):
    class _RefuseEverything(url_fetcher_class):
        """Every URL stops the rendering; nothing is opened, read or requested."""

        def fetch(self, url, headers=None):
            raise fatal_error(refused_url_message(str(url)))

    return _RefuseEverything(allowed_protocols=(), allow_redirects=False, fail_on_errors=True)


# ── Public functions ────────────────────────────────────────────────────────


def pdf_unavailable_reason() -> str:
    """Empty when PDFs can be produced here, otherwise the reason (for the operator)."""
    try:
        _load_weasyprint()
    except PdfError as exc:
        return str(exc)
    return ""


def render_pdf(page_html: str) -> bytes:
    """The PDF of one printable page; raises PdfError (never a partial or empty PDF)."""
    if not page_html.strip():
        raise PdfError("Leerer Beleg — PDF nicht erzeugt.")
    if len(page_html) > MAX_HTML_CHARS:
        raise PdfError("Beleg ist unerwartet gross — PDF nicht erzeugt.")
    html_class, url_fetcher_class, fatal_error = _load_weasyprint()
    with _render_lock:
        try:
            pdf = html_class(string=page_html, base_url=None,
                             url_fetcher=_blocking_fetcher(url_fetcher_class, fatal_error)).write_pdf()
        except fatal_error as exc:   # a BaseException in WeasyPrint: catch it by name
            raise PdfError(str(exc)) from None
        except Exception as exc:
            raise PdfError(f"PDF konnte nicht erzeugt werden: {type(exc).__name__}: {exc}") from exc
    if not pdf or not pdf.startswith(b"%PDF-"):
        raise PdfError("PDF konnte nicht erzeugt werden (leere Ausgabe).")
    if len(pdf) > MAX_PDF_BYTES:
        raise PdfError(f"PDF ist zu gross ({len(pdf) // 1024} kB) — nicht verwendet.")
    return pdf
