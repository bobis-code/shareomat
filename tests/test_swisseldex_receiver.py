# -*- coding: utf-8 -*-
"""Swisseldex receiver (SX-1): archive, duplicates, login gate, run log, restart - simulated server."""
from __future__ import annotations

import gzip
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta, timezone

import pytest

from shareomat.config import SwisseldexConfig
from shareomat.core.collector import swisseldex_receiver as rx
from shareomat.core.collector.swisseldex_receiver import (
    FILE_NEEDS_REVIEW,
    FILE_RECEIVED,
    KIND_SYNC,
    KIND_TEST,
    RESULT_ERROR,
    RESULT_OK,
    RESULT_WARNING,
    TRIGGER_AUTO,
    SwisseldexReceiver,
)
from tests.swisseldex_fake import TLS_EOF, FakeMailbox, session_factory

PASSWORD = "pw-geheim-123"


class Clock:
    def __init__(self, start: float | None = None) -> None:
        self.now = start if start is not None else time.time()

    def __call__(self) -> float:
        return self.now


def _config(**overrides) -> SwisseldexConfig:
    values = {"enabled": True, "username": "cem-user", "password": PASSWORD}
    values.update(overrides)
    return SwisseldexConfig(**values)


def _receiver(tmp_path, box, *, clock=None, config=None, **kwargs) -> SwisseldexReceiver:
    return SwisseldexReceiver(
        config or _config(), tmp_path / "swisseldex",
        session_factory=session_factory(box), clock=clock or Clock(), **kwargs,
    )


def _archived(tmp_path) -> list[str]:
    return sorted(p.name for p in (tmp_path / "swisseldex" / "received").iterdir())


# ── connection test ──


def test_connection_test_on_empty_mailbox_succeeds_and_changes_nothing(tmp_path):
    box = FakeMailbox()
    record = _receiver(tmp_path, box).run_now(KIND_TEST)
    assert record["result"] == RESULT_OK
    assert record["message"] == "Verbindung erfolgreich – 0 Datei(en) vorhanden."
    assert record["connection"] == "ok" and record["download"] == "-"
    assert box.retr_calls == [] and _archived(tmp_path) == []


def test_connection_test_never_downloads(tmp_path):
    box = FakeMailbox()
    box.put("E66.xml", b"<x/>")
    record = _receiver(tmp_path, box).run_now(KIND_TEST)
    assert record["listed"] == 1 and box.retr_calls == []


def test_wrong_password_shows_plain_message(tmp_path):
    import ftplib
    box = FakeMailbox(login_error=ftplib.error_perm("530 Login incorrect"))
    record = _receiver(tmp_path, box).run_now(KIND_TEST)
    assert record["result"] == RESULT_ERROR
    assert record["message"].startswith("Anmeldung fehlgeschlagen")
    assert record["connection"] == "error" and record["retryable"] is False


def test_unreachable_server(tmp_path):
    box = FakeMailbox(connect_error=OSError("Network is unreachable"))
    record = _receiver(tmp_path, box).run_now(KIND_SYNC)
    assert record["result"] == RESULT_ERROR and "nicht erreichbar" in record["message"]
    assert record["retryable"] is True


# ── sync and archive ──


def test_sync_empty_mailbox(tmp_path):
    record = _receiver(tmp_path, FakeMailbox()).run_now(KIND_SYNC)
    assert record["result"] == RESULT_OK
    assert record["message"] == "Verbindung erfolgreich. Postfach leer – keine neuen Dateien."


def test_sync_archives_new_file_unchanged(tmp_path):
    box = FakeMailbox()
    content = b"<E66>original</E66>"
    box.put("E66_202611.xml", content)
    receiver = _receiver(tmp_path, box)
    record = receiver.run_now(KIND_SYNC)
    sha = hashlib.sha256(content).hexdigest()
    assert record["downloaded"] == 1 and record["result"] == RESULT_OK
    assert "1 neue Datei(en)" in record["message"]
    stored = tmp_path / "swisseldex" / "received" / f"{sha[:16]}_E66_202611.xml"
    assert stored.read_bytes() == content
    entry = receiver.snapshot()["files"][0]
    assert entry["status"] == FILE_RECEIVED and entry["sha256"] == sha


def test_second_sync_does_not_download_known_files_again(tmp_path):
    box = FakeMailbox()
    box.put("a.xml", b"<a/>")
    receiver = _receiver(tmp_path, box)
    receiver.run_now(KIND_SYNC)
    record = receiver.run_now(KIND_SYNC)
    assert record["known"] == 1 and record["downloaded"] == 0
    assert box.retr_calls == ["a.xml"]


def test_same_content_under_new_name_is_a_duplicate_not_a_second_file(tmp_path):
    box = FakeMailbox()
    box.put("a.xml", b"<same/>")
    receiver = _receiver(tmp_path, box)
    receiver.run_now(KIND_SYNC)
    box.put("a-copy.xml", b"<same/>")
    record = receiver.run_now(KIND_SYNC)
    assert record["duplicates"] == 1 and record["downloaded"] == 0
    assert len(_archived(tmp_path)) == 1
    assert receiver.snapshot()["files"][0]["aliases"] == ["a-copy.xml"]


def test_same_name_with_new_content_is_kept_as_second_file(tmp_path):
    box = FakeMailbox()
    box.put("E66.xml", b"<v1/>", "20261207050000")
    receiver = _receiver(tmp_path, box)
    receiver.run_now(KIND_SYNC)
    box.put("E66.xml", b"<v2-corrected/>", "20261208050000")
    record = receiver.run_now(KIND_SYNC)
    assert record["downloaded"] == 1
    assert len(_archived(tmp_path)) == 2
    newest = receiver.snapshot()["files"][0]
    assert "anderer Inhalt" in newest["note"]


def test_listing_without_timestamps_still_deduplicates_by_content(tmp_path):
    box = FakeMailbox(mlsd_supported=False)
    box.put("a.xml", b"<a/>", "")
    receiver = _receiver(tmp_path, box)
    receiver.run_now(KIND_SYNC)
    record = receiver.run_now(KIND_SYNC)
    assert record["known"] == 1 and len(_archived(tmp_path)) == 1


def test_interrupted_download_leaves_nothing_and_is_fetched_next_time(tmp_path):
    box = FakeMailbox()
    box.put("a.xml", b"x" * 5000)
    box.broken_transfer["a.xml"] = (1000, ConnectionResetError("reset"))
    receiver = _receiver(tmp_path, box)
    record = receiver.run_now(KIND_SYNC)
    assert record["result"] == RESULT_ERROR and record["retryable"] is True
    assert _archived(tmp_path) == []
    del box.broken_transfer["a.xml"]
    record = receiver.run_now(KIND_SYNC)
    assert record["downloaded"] == 1 and len(_archived(tmp_path)) == 1


def test_unclean_tls_close_after_complete_download_keeps_the_file(tmp_path):
    box = FakeMailbox()
    box.put("a.xml", b"<ok/>")
    box.error_after_transfer["a.xml"] = TLS_EOF
    record = _receiver(tmp_path, box).run_now(KIND_SYNC)
    assert record["downloaded"] == 1 and record["result"] == RESULT_OK
    assert "nicht sauber beendet" in record["detail"]


def test_unsupported_or_unsafe_names_are_not_downloaded_but_shown(tmp_path):
    box = FakeMailbox()
    box.put("rechnung.pdf", b"%PDF")
    box.put("../escape.xml", b"<x/>")
    receiver = _receiver(tmp_path, box)
    record = receiver.run_now(KIND_SYNC)
    assert box.retr_calls == []
    assert record["review"] == 2 and record["result"] == RESULT_WARNING
    reasons = {s["name"]: s["reason"] for s in receiver.snapshot()["skipped"]}
    assert reasons == {"rechnung.pdf": "Dateiendung nicht unterstützt", "../escape.xml": "Dateiname nicht zulässig"}
    assert _archived(tmp_path) == []


def test_damaged_gz_is_archived_but_needs_review(tmp_path):
    box = FakeMailbox()
    box.put("E31.xml.gz", b"not really gzip")
    receiver = _receiver(tmp_path, box)
    record = receiver.run_now(KIND_SYNC)
    assert record["review"] == 1
    entry = receiver.snapshot()["files"][0]
    assert entry["status"] == FILE_NEEDS_REVIEW and "beschädigt" in entry["note"]


def test_valid_gz_is_received(tmp_path):
    box = FakeMailbox()
    box.put("E31.xml.gz", gzip.compress(b"<E31/>"))
    receiver = _receiver(tmp_path, box)
    assert receiver.run_now(KIND_SYNC)["result"] == RESULT_OK
    assert receiver.snapshot()["files"][0]["status"] == FILE_RECEIVED


def test_gz_above_unpack_limit_needs_review(tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "_MAX_UNPACKED_BYTES", 1000)
    box = FakeMailbox()
    box.put("bomb.xml.gz", gzip.compress(b"0" * 50_000))
    receiver = _receiver(tmp_path, box)
    receiver.run_now(KIND_SYNC)
    assert "512 MB" in receiver.snapshot()["files"][0]["note"]


def test_nothing_is_ever_deleted_on_the_server(tmp_path):
    box = FakeMailbox()
    box.put("a.xml", b"<a/>")
    _receiver(tmp_path, box).run_now(KIND_SYNC)
    assert list(box.files) == ["a.xml"]   # FakeFtp has no delete at all; the file is still there


# ── login gate, queue, worker ──


def test_login_gate_is_persisted_and_survives_a_restart(tmp_path):
    clock = Clock()
    box = FakeMailbox()
    _receiver(tmp_path, box, clock=clock).run_now(KIND_TEST)
    restarted = _receiver(tmp_path, box, clock=clock)
    assert restarted.login_wait_seconds() == pytest.approx(rx.MIN_LOGIN_INTERVAL_SECONDS)
    clock.now += rx.MIN_LOGIN_INTERVAL_SECONDS
    assert restarted.login_wait_seconds() == 0


def test_request_right_after_a_login_is_queued_not_refused(tmp_path):
    clock = Clock()
    receiver = _receiver(tmp_path, FakeMailbox(), clock=clock)
    receiver.run_now(KIND_TEST)
    outcome = receiver.request(KIND_SYNC)
    assert outcome.accepted and outcome.starts_in_seconds > 0
    assert "Geplant" in outcome.message and "eine Anmeldung pro Minute" in outcome.message


def test_only_one_queued_request(tmp_path):
    receiver = _receiver(tmp_path, FakeMailbox())
    assert receiver.request(KIND_SYNC).accepted
    second = receiver.request(KIND_SYNC)
    assert not second.accepted and "bereits" in second.message


def test_worker_runs_test_then_delayed_sync_one_login_per_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "MIN_LOGIN_INTERVAL_SECONDS", 0.4)
    box = FakeMailbox()
    box.put("a.xml", b"<a/>")
    login_times: list[float] = []
    factory = session_factory(box)

    def timed_factory():
        login_times.append(time.monotonic())
        return factory()

    receiver = SwisseldexReceiver(_config(), tmp_path / "swisseldex", session_factory=timed_factory)
    receiver.start()
    try:
        first = receiver.request(KIND_TEST)
        assert first.accepted
        assert receiver.wait_for_run(first.run_id, timeout=5)["result"] == RESULT_OK
        second = receiver.request(KIND_SYNC)
        assert second.accepted and second.starts_in_seconds > 0
        record = receiver.wait_for_run(second.run_id, timeout=5)
        assert record is not None and record["downloaded"] == 1
    finally:
        receiver.stop()
    assert len(login_times) == 2 and login_times[1] - login_times[0] >= 0.35


def test_missing_credentials_refuse_requests(tmp_path):
    receiver = _receiver(tmp_path, FakeMailbox(), config=_config(password=""))
    outcome = receiver.request(KIND_TEST)
    assert not outcome.accepted and "Passwort fehlt" in outcome.message


def test_stop_during_download_records_nothing(tmp_path):
    box = FakeMailbox()
    box.put("a.xml", b"q" * 300_000)
    receiver = _receiver(tmp_path, box)
    box.on_chunk = lambda name: receiver._stop.set()
    record = receiver.run_now(KIND_SYNC)
    assert record["result"] == RESULT_ERROR and record["retryable"] is False
    assert _archived(tmp_path) == []


# ── restart, state file ──


def test_leftover_partial_download_is_removed_at_start(tmp_path):
    received = tmp_path / "swisseldex" / "received"
    received.mkdir(parents=True)
    (received / ".incoming-deadbeef.part").write_bytes(b"half")
    _receiver(tmp_path, FakeMailbox())
    assert _archived(tmp_path) == []


def test_corrupt_state_is_never_reset(tmp_path):
    base = tmp_path / "swisseldex"
    base.mkdir()
    (base / "state.json").write_text("{not json", encoding="utf-8")
    receiver = _receiver(tmp_path, FakeMailbox())
    assert receiver.snapshot()["state_error"]
    outcome = receiver.request(KIND_SYNC)
    assert not outcome.accepted and "beschädigt" in outcome.message
    assert (base / "state.json").read_text(encoding="utf-8") == "{not json"


def test_run_log_is_bounded_and_survives_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(rx, "_MAX_RUNS", 3)
    clock = Clock()
    box = FakeMailbox()
    receiver = _receiver(tmp_path, box, clock=clock)
    for _ in range(5):
        receiver.run_now(KIND_TEST)
    runs = _receiver(tmp_path, box, clock=clock).snapshot()["runs"]
    assert [r["id"] for r in runs] == [5, 4, 3]


def test_password_never_reaches_state_file_or_log(tmp_path, caplog):
    import ftplib
    caplog.set_level(logging.DEBUG)
    box = FakeMailbox(login_error=ftplib.error_perm(f"530 bad password {PASSWORD}"))
    receiver = _receiver(tmp_path, box)
    receiver.run_now(KIND_TEST)
    state_text = (tmp_path / "swisseldex" / "state.json").read_text(encoding="utf-8")
    assert PASSWORD not in state_text
    assert PASSWORD not in caplog.text
    assert PASSWORD not in json.dumps(receiver.snapshot())
    assert PASSWORD not in repr(_config())


# ── automatic sync, retries, warning ──


def test_auto_sync_is_due_without_history_and_waits_an_interval_after(tmp_path):
    clock = Clock()
    receiver = _receiver(tmp_path, FakeMailbox(), clock=clock, config=_config(auto_sync=True, sync_interval_hours=6))
    job = receiver._take_job()
    assert job is not None and job[:2] == (KIND_SYNC, TRIGGER_AUTO)
    receiver._running = None
    receiver._execute(*job)
    clock.now += 3600
    assert receiver._take_job() is None


def test_auto_sync_off_never_runs_by_itself(tmp_path):
    receiver = _receiver(tmp_path, FakeMailbox())
    assert receiver._take_job() is None
    assert receiver.snapshot()["next_auto_at"] is None


def test_auto_retries_are_bounded_and_skip_refused_logins(tmp_path):
    clock = Clock()
    receiver = _receiver(tmp_path, FakeMailbox(), clock=clock, config=_config(auto_sync=True))
    transient = {"result": RESULT_ERROR, "retryable": True}
    receiver._plan_auto_retry(transient)
    assert receiver._auto_retry_at == clock.now + rx.AUTO_RETRY_DELAYS_SECONDS[0]
    receiver._plan_auto_retry(transient)
    assert receiver._auto_retry_at == clock.now + rx.AUTO_RETRY_DELAYS_SECONDS[1]
    receiver._plan_auto_retry(transient)
    assert receiver._auto_retry_at is None
    receiver._plan_auto_retry({"result": RESULT_ERROR, "retryable": False})
    assert receiver._auto_retry_at is None


def test_overdue_warning_appears_and_goes_away(tmp_path):
    shown: list[str] = []
    clock = Clock()
    box = FakeMailbox()
    receiver = _receiver(tmp_path, box, clock=clock, warn=shown.append, unwarn=shown.remove)
    receiver.run_now(KIND_SYNC)
    assert shown == []
    clock.now += timedelta(days=8).total_seconds()
    receiver._update_warning()
    assert len(shown) == 1 and "höchstens 30 Tage" in shown[0]
    receiver._state["last_sync_success_at"] = datetime.fromtimestamp(clock.now, timezone.utc).isoformat()
    receiver._update_warning()
    assert shown == []
