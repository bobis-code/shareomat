# -*- coding: utf-8 -*-
"""
File: shareomat/web/server.py

Purpose:
    Minimal HTTP server for the Shareomat admin web interface — works
    standalone (Docker) and behind Home Assistant Ingress. Owns HTTP
    routing, file upload/download handling, and server lifecycle.
    Replaces the old shareomat/ha/ingress.py.

Part of:
    Shareomat — Swiss LEG/ZEV Settlement Engine

Notes:
    No separate router/request/response framework — routing is a plain
    dict dispatch by the first path segment, and each web/pages/* module
    handles its own sub-routes (new/edit/toggle) via ctx.segments. See
    SHAREOMAT_UMBAU_STRUKTUR.md §10.

    Data-changing requests only happen via POST, and every POST is
    checked against the process's CSRF token (see web/rendering.py).
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from html import escape as _html_escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from shareomat.web.navigation import url_for
from shareomat.web.pages import (
    automation,
    billing,
    community,
    dashboard,
    external_data,
    invoices,
    meter_data,
    meters,
    participants,
    reports as reports_page,
    settings as settings_page,
    setup,
    tariffs,
)
from shareomat.web.rendering import FormError, Redirect, RequestContext, check_csrf
from shareomat.web.reports import report_path
from shareomat.web.state import get_state

logger = logging.getLogger(__name__)

__all__ = ["WebServer", "get_state"]

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_ALLOWED_UPLOAD_EXT = {".csv", ".xml", ".xlsx"}
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_SOCKET_TIMEOUT_SECONDS = 30
_UPLOAD_CHUNK_SIZE = 64 * 1024  # 64 KB chunks for binary streaming
_MAX_UPLOAD_SECONDS = 120  # Total time budget per upload

_PAGE_MODULES = {
    "": dashboard,
    "setup": setup,
    "community": community,
    "participants": participants,
    "meters": meters,
    "tariffs": tariffs,
    "meter_data": meter_data,
    "billing": billing,
    "invoices": invoices,
    "automation": automation,
    "reports": reports_page,
    "settings": settings_page,
    "external_data": external_data,
}


def _flatten_qs(query: str) -> dict[str, str]:
    """Parse a query/body string into a single-value-per-key dict (last value wins)."""
    parsed = parse_qs(query, keep_blank_values=True)
    return {k: v[-1] for k, v in parsed.items()}


class _Handler(BaseHTTPRequestHandler):
    """HTTP request handler for the Shareomat admin web interface.

    Home Assistant handles authentication before proxying to this handler
    when running as an add-on; standalone deployments are expected to sit
    behind their own network boundary (see README).
    """

    def setup(self) -> None:
        super().setup()
        self.connection.settimeout(_SOCKET_TIMEOUT_SECONDS)

    def log_message(self, fmt, *args) -> None:
        logger.debug("Web: " + fmt, *args)

    def _ingress_path(self) -> str:
        """Return the HA proxy path prefix so forms and links work behind Ingress."""
        return self.headers.get("X-Ingress-Path", "").rstrip("/")

    def _send_html(self, html: str, *, status: int = 200) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        self.end_headers()

    def _serve_static_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        size = path.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        with path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile, length=1024 * 1024)

    def _serve_download(self, query: str) -> None:
        """Serve a report file as a download, constrained to the reports directory."""
        params = parse_qs(query)
        filename = params.get("f", [""])[0]
        file_path = report_path(filename)

        if file_path is None:
            self.send_response(404)
            self.end_headers()
            return

        suffix = file_path.suffix.lower()
        mime = {
            ".csv": "text/csv; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        }.get(suffix, "application/octet-stream")

        safe_name = Path(filename).name
        ascii_name = safe_name.encode("ascii", errors="ignore").decode("ascii") or "report"
        encoded_name = quote(safe_name)
        size = file_path.stat().st_size

        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header(
            "Content-Disposition",
            f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{encoded_name}',
        )
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with file_path.open("rb") as f:
            shutil.copyfileobj(f, self.wfile, length=1024 * 1024)
        logger.debug("Download served: %s (%d bytes)", safe_name, size)

    # ── GET ──────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        ingress_path = self._ingress_path()

        if path == "/static/app.css":
            self._serve_static_file(_STATIC_DIR / "app.css", "text/css; charset=utf-8")
            return
        if path == "/static/banner.png":
            self._serve_static_file(_STATIC_DIR / "banner.png", "image/png")
            return
        if path == "/reports/download":
            self._serve_download(parsed.query)
            return

        segments = [s for s in path.split("/") if s]
        # URLs use hyphens ("meter-data"), but route names (_PAGE_MODULES keys,
        # ROUTE_PATHS keys, url_for() arguments) use underscores throughout —
        # normalize once here so route always means the same thing everywhere.
        route = segments[0].replace("-", "_") if segments else ""
        page = _PAGE_MODULES.get(route)
        if page is None:
            self.send_response(404)
            self.end_headers()
            return

        ctx = RequestContext(
            method="GET",
            ingress_path=ingress_path,
            segments=segments[1:],
            query=_flatten_qs(parsed.query),
        )
        try:
            html = page.handle_get(ctx)
        except Exception:
            # Never let an unexpected exception kill the connection with no response at
            # all — behind a reverse proxy (e.g. HA Ingress) that shows up as a bare
            # "502 Bad Gateway" with no clue what happened. Log the real traceback and
            # return a page that at least explains something broke.
            logger.exception("Unhandled error rendering GET %s", path)
            self._send_html(
                "<h1>Interner Fehler</h1><p>Beim Laden der Seite ist ein unerwarteter Fehler "
                "aufgetreten. Details stehen im Add-on-Log.</p>",
                status=500,
            )
            return
        self._send_html(html)

    # ── POST ─────────────────────────────────────────────────────────────

    def _parse_urlencoded_form(self) -> dict[str, str]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        body = self.rfile.read(length).decode("utf-8", errors="replace")
        return _flatten_qs(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        ingress_path = self._ingress_path()
        content_type = self.headers.get("Content-Type", "")

        if path == "/actions/run":
            form = self._parse_urlencoded_form()
            ctx = RequestContext(method="POST", ingress_path=ingress_path, form=form)
            try:
                check_csrf(ctx)
            except FormError as exc:
                get_state().set_flash(str(exc), ok=False)
                self._redirect(url_for(ingress_path, "meter_data"))
                return
            if get_state().trigger_run():
                logger.info("Web: manual run triggered")
            else:
                get_state().set_flash(
                    "Lauf nicht gestartet: Es läuft bereits ein Abrechnungslauf.", ok=False,
                )
            self._redirect(url_for(ingress_path, "meter_data"))
            return

        if path == "/meter-data/upload":
            self._handle_upload(ingress_path, content_type)
            return

        segments = [s for s in path.split("/") if s]
        route = segments[0].replace("-", "_") if segments else ""
        page = _PAGE_MODULES.get(route)
        if page is None or not hasattr(page, "handle_post"):
            self.send_response(404)
            self.end_headers()
            return

        form = self._parse_urlencoded_form()
        ctx = RequestContext(method="POST", ingress_path=ingress_path, segments=segments[1:], form=form)

        try:
            check_csrf(ctx)
            redirect_to = page.handle_post(ctx)
        except FormError as exc:
            get_state().set_flash(str(exc), ok=False)
            redirect_to = None
        except Redirect as exc:
            redirect_to = exc.location
        except Exception:
            # Same reasoning as do_GET: an unhandled exception here must never just
            # drop the connection (looks like "502 Bad Gateway" through a proxy) — turn
            # it into a normal flash-and-redirect so the user sees *something* and the
            # real cause is in the log.
            logger.exception("Unhandled error handling POST %s", path)
            get_state().set_flash(
                "Unerwarteter Fehler — Details stehen im Add-on-Log.", ok=False,
            )
            redirect_to = None

        if redirect_to is None:
            redirect_to = url_for(ingress_path, route)
        self._redirect(redirect_to)

    # ── File upload (multipart, streamed) ───────────────────────────────

    def _handle_upload(self, ingress_path: str, content_type: str) -> None:
        """Parse the meter-data upload form (file + CSRF token) and save it to the inbox."""
        runtime = get_state().runtime
        redirect_target = url_for(ingress_path, "meter_data")

        if runtime is None:
            get_state().set_flash("Upload fehlgeschlagen: System noch nicht bereit.", ok=False)
            self._redirect(redirect_target)
            return

        inbox = runtime.paths.inbox
        try:
            inbox.mkdir(parents=True, exist_ok=True)
            result = self._save_file_upload(inbox, content_type)

            if result is None:
                get_state().set_flash("Upload fehlgeschlagen: keine gültige Datei erhalten.", ok=False)
                self._redirect(redirect_target)
                return

            csrf_token_received, filename, size = result
            if csrf_token_received != get_state().csrf_token:
                get_state().set_flash(
                    "Ungültige Anfrage (Sicherheits-Token fehlt oder ist abgelaufen). "
                    "Bitte die Seite neu laden und erneut versuchen.", ok=False,
                )
                self._redirect(redirect_target)
                return

            logger.info("Upload: saved %s (%d bytes) to inbox", filename, size)
            try:
                get_state().update(inbox_count=sum(1 for f in inbox.iterdir() if f.is_file()))
            except Exception as exc:
                logger.warning("Upload: inbox counter refresh failed: %s", exc)
            get_state().set_flash(
                f"&#10003; Hochgeladen: <strong>{_html_escape(filename)}</strong> "
                f"({size:,} bytes) &rarr; Eingang", ok=True,
            )
        except Exception as exc:
            logger.error("Upload failed: %s", exc)
            get_state().set_flash(f"Upload fehlgeschlagen: {_html_escape(str(exc))}", ok=False)

        self._redirect(redirect_target)

    def _save_file_upload(self, inbox: Path, content_type: str) -> tuple[str, str, int] | None:
        """Parse multipart/form-data and stream the uploaded file to inbox.

        Returns (csrf_token, filename, bytes_written) or None if the
        request had no valid file part. The parser is intentionally small
        and dependency-free — Shareomat's web layer uses only the Python
        standard library plus Jinja2.
        """
        if "multipart/form-data" not in content_type:
            return None

        boundary = b""
        for segment in content_type.split(";"):
            seg = segment.strip()
            if seg.lower().startswith("boundary="):
                boundary = seg[9:].strip('"').encode()
                break
        if not boundary:
            return None

        length = int(self.headers.get("Content-Length", 0))
        if length <= 0 or length > _MAX_UPLOAD_BYTES:
            logger.warning("Upload: invalid Content-Length %d", length)
            return None

        remaining = length
        deadline = time.monotonic() + _MAX_UPLOAD_SECONDS
        csrf_token = ""

        def _check_deadline() -> None:
            if time.monotonic() > deadline:
                raise TimeoutError("Upload exceeded total time limit")

        def _readline() -> bytes:
            nonlocal remaining
            if remaining <= 0:
                return b""
            _check_deadline()
            line = self.rfile.readline(remaining)
            remaining -= len(line)
            return line

        def _read_chunk() -> bytes:
            nonlocal remaining
            if remaining <= 0:
                return b""
            _check_deadline()
            n = min(_UPLOAD_CHUNK_SIZE, remaining)
            data = self.rfile.read(n)
            remaining -= len(data)
            return data

        boundary_line = b"--" + boundary
        end_boundary_line = boundary_line + b"--"
        body_boundary = b"\r\n--" + boundary

        while remaining > 0:
            line = _readline().rstrip(b"\r\n")
            if line == boundary_line:
                break
            if line == end_boundary_line:
                return None
        else:
            return None

        filename = ""
        while remaining > 0:
            headers: list[str] = []
            while remaining > 0:
                line = _readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                headers.append(line.decode("utf-8", errors="ignore").strip())

            part_name = ""
            part_filename = ""
            for header_line in headers:
                if "Content-Disposition" not in header_line:
                    continue
                for token in header_line.split(";"):
                    token = token.strip()
                    if token.lower().startswith("name=") and not token.lower().startswith("filename="):
                        part_name = token[5:].strip('"').strip("'").strip()
                    elif token.lower().startswith("filename="):
                        part_filename = Path(token[9:].strip('"').strip("'").strip()).name

            if part_filename:
                filename = part_filename
                break

            # Non-file field: read its (short) value, remembering csrf_token, then skip to next part.
            value_lines: list[bytes] = []
            found_next = False
            while remaining > 0:
                line = _readline()
                stripped = line.rstrip(b"\r\n")
                if stripped == boundary_line:
                    found_next = True
                    break
                if stripped == end_boundary_line:
                    return None
                value_lines.append(stripped)
            if not found_next:
                return None
            if part_name == "csrf_token":
                csrf_token = b"".join(value_lines).decode("utf-8", errors="ignore")
        else:
            return None

        if not filename:
            return None

        ext = Path(filename).suffix.lower()
        if ext not in _ALLOWED_UPLOAD_EXT:
            logger.warning("Upload: rejected file type '%s'", ext)
            raise ValueError(f"unsupported file type '{ext}'. Use .csv, .xml, or .xlsx.")

        dest = inbox / filename
        temp_dest = inbox / f".{filename}.upload-{uuid.uuid4().hex}.tmp"
        total = 0
        overlap = len(body_boundary)
        buf = b""
        found_boundary = False

        try:
            with temp_dest.open("wb") as f:
                while True:
                    chunk = _read_chunk()
                    buf += chunk
                    idx = buf.find(body_boundary)
                    if idx != -1:
                        if idx > 0:
                            f.write(buf[:idx])
                            total += idx
                        found_boundary = True
                        break
                    safe = len(buf) - overlap
                    if safe > 0:
                        f.write(buf[:safe])
                        total += safe
                        buf = buf[safe:]
                    if not chunk:
                        break

            if not found_boundary:
                return None

            temp_dest.replace(dest)
            return csrf_token, filename, total
        finally:
            if temp_dest.exists():
                temp_dest.unlink()


class WebServer(threading.Thread):
    """Daemon thread running the admin web server (standalone or behind HA Ingress)."""

    def __init__(self, port: int = 8099) -> None:
        super().__init__(name="shareomat-web", daemon=True)
        self._port = port
        self._server: ThreadingHTTPServer | None = None

    def stop(self) -> None:
        """Gracefully shut down the HTTP server."""
        if self._server:
            self._server.shutdown()

    def run(self) -> None:
        try:
            self._server = ThreadingHTTPServer(("0.0.0.0", self._port), _Handler)
            logger.info("Web server listening on port %d", self._port)
            self._server.serve_forever()
        except Exception as exc:
            logger.error("Web server error: %s", exc)
