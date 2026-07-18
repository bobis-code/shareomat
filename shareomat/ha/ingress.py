# -*- coding: utf-8 -*-
"""
Minimal HTTP server for the Home Assistant Ingress interface.

This module owns HTTP routing, file upload/download handling, and server
lifecycle. Shared dashboard state, report rendering, and HTML rendering live in
smaller sibling modules so the interface can grow without turning this file
into the whole web application.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import uuid
from html import escape as _html_escape
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from shareomat.ha.web_render import render_dashboard
from shareomat.ha.web_reports import report_path
from shareomat.ha.web_state import IngressState, get_state

logger = logging.getLogger(__name__)

__all__ = ["IngressServer", "IngressState", "get_state"]

_ALLOWED_UPLOAD_EXT = {".csv", ".xml", ".xlsx"}
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
_SOCKET_TIMEOUT_SECONDS = 30
_UPLOAD_CHUNK_SIZE = 64 * 1024  # 64 KB chunks for binary streaming
_MAX_UPLOAD_SECONDS = 120  # Total time budget per upload


class _IngressHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the Home Assistant Ingress web UI.

    Home Assistant handles authentication before proxying to this handler. The
    handler therefore focuses on local dashboard actions: display, upload,
    download, and manual run trigger.
    """

    def setup(self) -> None:
        """Initialize the socket and cap blocking reads from slow clients."""
        super().setup()
        self.connection.settimeout(_SOCKET_TIMEOUT_SECONDS)

    def log_message(self, fmt, *args) -> None:
        """Route BaseHTTPRequestHandler logs through the project logger."""
        logger.debug("Ingress: " + fmt, *args)

    def _ingress_path(self) -> str:
        """Return the HA proxy path prefix so forms and links work behind Ingress."""
        return self.headers.get("X-Ingress-Path", "").rstrip("/")

    def _redirect_home(self) -> None:
        self.send_response(303)
        self.send_header("Location", self._ingress_path() + "/")
        self.end_headers()

    def _serve_download(self, query: str) -> None:
        """Serve a report file as a download.

        The report_path helper constrains downloads to known files inside the
        configured reports directory.
        """
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
        logger.debug("Ingress download: served %s (%d bytes)", safe_name, size)

    def do_GET(self) -> None:
        """Serve the dashboard or a report file download."""
        parsed = urlparse(self.path)
        if parsed.path.rstrip("/").endswith("/download"):
            self._serve_download(parsed.query)
            return

        query = parse_qs(parsed.query)
        selected_report = query.get("report", [""])[0]
        body = render_dashboard(self._ingress_path(), selected_report).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        """Handle manual runs and meter file uploads."""
        path = urlparse(self.path).path.rstrip("/")
        if path.endswith("/run"):
            triggered = get_state().trigger_run()
            if triggered:
                logger.info("Ingress: manual run triggered")
            else:
                get_state().set_upload_result(
                    "Run not started: another settlement run is already active.",
                    ok=False,
                )
            self._redirect_home()
            return

        if path.endswith("/upload"):
            self._handle_upload()
            return

        self.send_response(404)
        self.end_headers()

    def _handle_upload(self) -> None:
        """Save an uploaded meter data file to the engine inbox."""
        state = get_state()
        inbox = state.inbox_path
        if inbox is None:
            logger.error("Ingress upload: inbox path not registered")
            state.set_upload_result("Upload failed: inbox path not configured.", ok=False)
            self._redirect_home()
            return

        try:
            inbox.mkdir(parents=True, exist_ok=True)
            result = self._save_file_upload(inbox)

            if result is None:
                logger.warning("Ingress upload: no valid file in request")
                state.set_upload_result("Upload failed: no valid file received.", ok=False)
                self._redirect_home()
                return

            filename, size = result
            logger.info("Ingress upload: saved %s (%d bytes) to inbox", filename, size)

            # Refresh the dashboard counter immediately; a later engine run will update it again.
            try:
                state.update(inbox_count=sum(1 for f in inbox.iterdir() if f.is_file()))
            except Exception as exc:
                logger.warning("Ingress upload: inbox counter refresh failed: %s", exc)
            state.set_upload_result(
                f"&#10003; Uploaded: <strong>{_html_escape(filename)}</strong> "
                f"({size:,} bytes) &rarr; inbox",
                ok=True,
            )
        except Exception as exc:
            logger.error("Ingress upload failed: %s", exc)
            state.set_upload_result(f"Upload failed: {_html_escape(str(exc))}", ok=False)

        self._redirect_home()

    def _save_file_upload(self, inbox: Path) -> tuple[str, int] | None:
        """Parse multipart/form-data and stream the uploaded file to inbox.

        The parser is intentionally small and dependency-free because the add-on
        currently uses only Python's standard library for Ingress.
        """
        content_type = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in content_type:
            return None

        # Multipart boundaries are declared in Content-Type, e.g. boundary=----abc.
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
            logger.warning("Ingress upload: invalid Content-Length %d", length)
            return None

        remaining = length
        deadline = time.monotonic() + _MAX_UPLOAD_SECONDS

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
        # File body ends with \r\n--boundary; the \r\n belongs to the delimiter, not the content.
        body_boundary = b"\r\n--" + boundary

        # Find the opening boundary.
        while remaining > 0:
            line = _readline().rstrip(b"\r\n")
            if line == boundary_line:
                break
            if line == end_boundary_line:
                return None
        else:
            return None

        # Loop over parts until we find one that carries a file.
        # Non-file fields (hidden inputs, text) are skipped.
        filename = ""
        while remaining > 0:
            # Read MIME headers for this part.
            headers: list[str] = []
            while remaining > 0:
                line = _readline()
                if line in (b"\r\n", b"\n", b""):
                    break
                headers.append(line.decode("utf-8", errors="ignore").strip())

            part_filename = ""
            for header_line in headers:
                if "Content-Disposition" not in header_line or "filename=" not in header_line:
                    continue
                for token in header_line.split(";"):
                    token = token.strip()
                    if token.lower().startswith("filename="):
                        part_filename = Path(token[9:].strip('"').strip("'").strip()).name
                        break

            if part_filename:
                filename = part_filename
                break

            # Skip body of this non-file part (text fields are small; readline is safe).
            found_next = False
            while remaining > 0:
                line = _readline()
                stripped = line.rstrip(b"\r\n")
                if stripped == boundary_line:
                    found_next = True
                    break
                if stripped == end_boundary_line:
                    return None
            if not found_next:
                return None
        else:
            return None

        if not filename:
            return None

        ext = Path(filename).suffix.lower()
        if ext not in _ALLOWED_UPLOAD_EXT:
            logger.warning("Ingress upload: rejected file type '%s'", ext)
            raise ValueError(f"unsupported file type '{ext}'. Use .csv, .xml, or .xlsx.")

        dest = inbox / filename
        temp_dest = inbox / f".{filename}.upload-{uuid.uuid4().hex}.tmp"
        total = 0
        overlap = len(body_boundary)
        buf = b""
        found_boundary = False

        # Stream the file body in fixed-size chunks, scanning for the boundary.
        # readline() is unsafe for binary content (XLSX etc.) because data without \n
        # is returned as one block up to _MAX_UPLOAD_BYTES.
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
                    # Flush bytes that cannot be part of a split boundary.
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
            return filename, total
        finally:
            if temp_dest.exists():
                temp_dest.unlink()


class IngressServer(threading.Thread):
    """Daemon thread running the ingress HTTP server."""

    def __init__(self, port: int = 8099) -> None:
        super().__init__(name="shareomat-ingress", daemon=True)
        self._port = port
        self._server: HTTPServer | None = None

    def stop(self) -> None:
        """Gracefully shut down the HTTP server."""
        if self._server:
            self._server.shutdown()

    def run(self) -> None:
        """Start HTTPServer and serve until stop() is called."""
        try:
            self._server = HTTPServer(("0.0.0.0", self._port), _IngressHandler)
            logger.info("Ingress server listening on port %d", self._port)
            self._server.serve_forever()
        except Exception as exc:
            logger.error("Ingress server error: %s", exc)
