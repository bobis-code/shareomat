# -*- coding: utf-8 -*-
"""Swisseldex receiver with the raw backup (ADR-0010): backup first, then archive, then import - simulated server and mount."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from shareomat.core.collector import swisseldex_receiver as rx
from shareomat.core.collector.raw_backup import PROOF_SUFFIX, RawBackupGuard
from shareomat.core.collector.swisseldex_receiver import (
    BACKUP_BYPASS,
    BACKUP_ERROR,
    BACKUP_OK,
    FILE_NEEDS_REVIEW,
    KIND_SYNC,
    RESULT_OK,
    RESULT_WARNING,
)
from shareomat.leg_const import RAW_BACKUP_MODE_OFF, RAW_BACKUP_MODE_REQUIRED
from tests.swisseldex_fake import FakeMailbox
from tests.test_swisseldex_receiver import _receiver

FILE_A = "20261120_155940_12X-SENDER-TEST_E66_12X-RECEIVER-TS_008.xml"
FILE_B = "20261121_160448_12X-SENDER-TEST_E66_12X-RECEIVER-TS_008.xml"
CONTENT_A = b"<SDAT>delivery A</SDAT>\n"
CONTENT_B = b"<SDAT>delivery B</SDAT>\n"


class Setup:
    """Share root with one storage 'omv'; mounting and the mode can be changed during a test."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "share"
        (self.root / "omv").mkdir(parents=True)
        self.mounts = tmp_path / "mounts"
        self.mode = RAW_BACKUP_MODE_REQUIRED
        self.mount(True)
        self.guard = RawBackupGuard(lambda: (self.mode, "omv", "swisseldex-rohdaten"),
                                    share_root=self.root, mounts_file=self.mounts)
        self.imported: list[list[str]] = []
        self.warnings: list[str] = []

    def mount(self, mounted: bool) -> None:
        fstype = "cifs" if mounted else "autofs"
        self.mounts.write_text(f"//192.0.2.10/sicherung {self.root / 'omv'} {fstype} rw 0 0\n", encoding="utf-8")

    def importer(self, files, automatic):
        self.imported.append([name for _, _, name in files])
        return []

    def receiver(self, tmp_path, box, **kwargs):
        return _receiver(tmp_path, box, raw_backup=self.guard, importer=self.importer,
                         warn=self.warnings.append, unwarn=self.warnings.remove, **kwargs)

    def on_storage(self) -> list[str]:
        folder = self.root / "omv" / "swisseldex-rohdaten"
        return sorted(p.name for p in folder.rglob("*") if p.is_file()) if folder.exists() else []


@pytest.fixture
def env(tmp_path):
    return Setup(tmp_path)


def _box(*files):
    box = FakeMailbox()
    for name, content in files:
        box.put(name, content)
    return box


def _entry(receiver, name):
    return next(f for f in receiver.snapshot()["files"] if f["name"] == name)


# ── normal case ──


def test_backup_first_then_archive_then_import(env, tmp_path):
    box = _box((FILE_A, CONTENT_A))
    receiver = env.receiver(tmp_path, box)
    record = receiver.run_now(KIND_SYNC)
    assert record["result"] == RESULT_OK and record["backed_up"] == 1 and record["downloaded"] == 1
    copy = env.root / "omv" / "swisseldex-rohdaten" / "2026-11" / FILE_A
    assert copy.read_bytes() == CONTENT_A
    assert json.loads((copy.parent / (FILE_A + PROOF_SUFFIX)).read_text())["sha256"] == \
        hashlib.sha256(CONTENT_A).hexdigest()
    entry = _entry(receiver, FILE_A)
    assert entry["raw_backup"]["status"] == BACKUP_OK and entry["raw_backup"]["verified"]
    assert entry["raw_backup"]["path"] == f"swisseldex-rohdaten/2026-11/{FILE_A}"
    assert env.imported == [[FILE_A]] and env.warnings == []
    assert "1 auf dem Netzwerkspeicher gesichert" in record["message"]


def test_all_downloads_finish_before_the_first_backup(env, tmp_path):
    """The Datahub drops idle sessions after 10 s: no backup while the session is open."""
    box = _box((FILE_A, CONTENT_A), (FILE_B, CONTENT_B))
    downloads_at_backup = []
    real = env.guard.backup

    def watching(*args, **kwargs):
        downloads_at_backup.append(len(box.retr_calls))
        return real(*args, **kwargs)

    env.guard.backup = watching
    env.receiver(tmp_path, box).run_now(KIND_SYNC)
    assert downloads_at_backup == [2, 2]


# ── the key property: no import without a verified backup ──


def test_no_import_while_the_storage_is_not_mounted_then_retry_succeeds(env, tmp_path):
    env.mount(False)
    box = _box((FILE_A, CONTENT_A))
    receiver = env.receiver(tmp_path, box)
    record = receiver.run_now(KIND_SYNC)
    assert record["result"] == RESULT_WARNING and record["backup_failed"] == 1 and record["downloaded"] == 0
    assert receiver.snapshot()["files"] == [] and receiver.received_files() == []
    assert env.imported == [[]] and env.on_storage() == []              # importer saw nothing
    assert not any((tmp_path / "swisseldex" / "received").iterdir())    # not even archived
    assert receiver.snapshot()["backup_failures"][0]["name"] == FILE_A
    assert any("Rohdatensicherung" in w for w in env.warnings)
    assert "NICHT gesichert" in record["message"]

    env.mount(True)                                                    # storage back: next sync downloads again
    record = receiver.run_now(KIND_SYNC)
    assert box.retr_calls.count(FILE_A) == 2 and record["backed_up"] == 1
    assert env.imported[-1] == [FILE_A] and receiver.snapshot()["backup_failures"] == []
    assert env.warnings == []


def test_import_button_offers_no_file_without_backup(env, tmp_path):
    env.mode = RAW_BACKUP_MODE_OFF
    receiver = env.receiver(tmp_path, _box((FILE_A, CONTENT_A)))
    receiver.run_now(KIND_SYNC)                                        # received in the transition mode
    env.mode, env.imported = RAW_BACKUP_MODE_REQUIRED, []
    env.mount(False)
    receiver.import_now()
    assert env.imported == [[]] and receiver.received_files() == []
    assert _entry(receiver, FILE_A)["raw_backup"]["status"] == BACKUP_ERROR


def test_write_error_on_the_storage_blocks_the_file(env, tmp_path, monkeypatch):
    from shareomat.core.collector import raw_backup as rb

    def full(fd, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(rb, "_write_all", full)
    receiver = env.receiver(tmp_path, _box((FILE_A, CONTENT_A)))
    record = receiver.run_now(KIND_SYNC)
    assert record["backup_failed"] == 1 and receiver.received_files() == []
    assert "No space left" in receiver.snapshot()["backup_failures"][0]["message"]


def test_wrong_checksum_after_copy_blocks_the_file(env, tmp_path, monkeypatch):
    from shareomat.core.collector import raw_backup as rb
    real = rb._sha256_of
    omv = env.root / "omv"

    def lying(path, limit=rb.MAX_BACKUP_BYTES):
        sha, size = real(path, limit)
        return ("f" * 64, size) if omv in Path(path).parents else (sha, size)

    monkeypatch.setattr(rb, "_sha256_of", lying)
    receiver = env.receiver(tmp_path, _box((FILE_A, CONTENT_A)))
    assert receiver.run_now(KIND_SYNC)["backup_failed"] == 1
    assert receiver.received_files() == [] and env.imported == [[]]


# ── transition mode, backfill, restart ──


def test_transition_mode_works_as_before_and_says_so(env, tmp_path):
    env.mode = RAW_BACKUP_MODE_OFF
    receiver = env.receiver(tmp_path, _box((FILE_A, CONTENT_A)))
    record = receiver.run_now(KIND_SYNC)
    assert record["backup_bypass"] == 1 and env.imported == [[FILE_A]] and env.on_storage() == []
    assert _entry(receiver, FILE_A)["raw_backup"] == {"status": BACKUP_BYPASS}
    assert "Rohdatensicherung ausgeschaltet" in record["message"]


def test_files_received_before_are_backed_up_from_the_archive(env, tmp_path):
    env.mode = RAW_BACKUP_MODE_OFF
    box = _box((FILE_A, CONTENT_A))
    receiver = env.receiver(tmp_path, box)
    receiver.run_now(KIND_SYNC)
    env.mode = RAW_BACKUP_MODE_REQUIRED
    record = receiver.run_now(KIND_SYNC)                              # nothing new on the server
    assert record["backfilled"] == 1 and box.retr_calls.count(FILE_A) == 1
    entry = _entry(receiver, FILE_A)
    assert entry["raw_backup"]["status"] == BACKUP_OK and entry["raw_backup"]["backfilled"]
    assert (env.root / "omv" / "swisseldex-rohdaten" / "2026-11" / FILE_A).read_bytes() == CONTENT_A


def test_changed_archive_file_is_not_passed_off_as_the_original(env, tmp_path):
    env.mode = RAW_BACKUP_MODE_OFF
    receiver = env.receiver(tmp_path, _box((FILE_A, CONTENT_A)))
    receiver.run_now(KIND_SYNC)
    archived = tmp_path / "swisseldex" / "received" / _entry(receiver, FILE_A)["stored_as"]
    archived.write_bytes(b"<SDAT>tampered</SDAT>\n")
    env.mode = RAW_BACKUP_MODE_REQUIRED
    record = receiver.run_now(KIND_SYNC)
    entry = _entry(receiver, FILE_A)
    assert record["backup_failed"] == 1 and entry["raw_backup"]["status"] == BACKUP_ERROR
    assert "nicht mehr dem empfangenen Original" in entry["raw_backup"]["message"]
    assert env.on_storage() == [] and receiver.received_files() == []


def test_restart_after_copy_before_proof_completes_the_backup(env, tmp_path):
    folder = env.root / "omv" / "swisseldex-rohdaten" / "2026-11"
    folder.mkdir(parents=True)
    (folder / FILE_A).write_bytes(CONTENT_A)                           # copy written, proof missing
    receiver = env.receiver(tmp_path, _box((FILE_A, CONTENT_A)))
    record = receiver.run_now(KIND_SYNC)
    assert record["backed_up"] == 1 and (folder / (FILE_A + PROOF_SUFFIX)).exists()
    assert env.imported == [[FILE_A]]


def test_interrupted_copy_is_finished_by_the_next_sync(env, tmp_path, monkeypatch):
    from shareomat.core.collector import raw_backup as rb
    real_write = rb._write_all

    def broken(fd, data):
        real_write(fd, data[:7])
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(rb, "_write_all", broken)
    box = _box((FILE_A, CONTENT_A))
    receiver = env.receiver(tmp_path, box)
    first = receiver.run_now(KIND_SYNC)
    assert first["backup_failed"] == 1 and receiver.received_files() == []
    assert "automatisch fort" in receiver.snapshot()["backup_failures"][0]["message"]

    monkeypatch.setattr(rb, "_write_all", real_write)
    second = receiver.run_now(KIND_SYNC)
    assert second["backed_up"] == 1 and env.imported[-1] == [FILE_A] and env.warnings == []
    copy = env.root / "omv" / "swisseldex-rohdaten" / "2026-11" / FILE_A
    assert copy.read_bytes() == CONTENT_A and (copy.parent / (FILE_A + PROOF_SUFFIX)).exists()


def test_same_name_other_content_on_the_storage_is_never_overwritten(env, tmp_path):
    folder = env.root / "omv" / "swisseldex-rohdaten" / "2026-11"
    folder.mkdir(parents=True)
    (folder / FILE_A).write_bytes(b"<SDAT>something else</SDAT>\n")
    receiver = env.receiver(tmp_path, _box((FILE_A, CONTENT_A)))
    record = receiver.run_now(KIND_SYNC)
    assert record["backup_failed"] == 1 and receiver.received_files() == []
    assert (folder / FILE_A).read_bytes() == b"<SDAT>something else</SDAT>\n"
    assert "So lösen" in receiver.snapshot()["backup_failures"][0]["message"]


# ── gzip and import failures ──


def test_broken_gzip_is_backed_up_before_it_is_read(env, tmp_path, monkeypatch):
    name = FILE_A + ".gz"
    broken = gzip.compress(CONTENT_A)[:-6]
    seen_on_storage = []
    real = rx._gzip_problem

    def checking(path):
        seen_on_storage.append(name in env.on_storage())
        return real(path)

    monkeypatch.setattr(rx, "_gzip_problem", checking)
    receiver = env.receiver(tmp_path, _box((name, broken)))
    record = receiver.run_now(KIND_SYNC)
    assert seen_on_storage == [True]                                   # original saved before the first read
    assert (env.root / "omv" / "swisseldex-rohdaten" / "2026-11" / name).read_bytes() == broken
    assert _entry(receiver, name)["status"] == FILE_NEEDS_REVIEW and record["review"] == 1


def test_failed_import_keeps_the_verified_backup(env, tmp_path):
    def failing(files, automatic):
        raise RuntimeError("import broke")

    receiver = _receiver(tmp_path, _box((FILE_A, CONTENT_A)), raw_backup=env.guard, importer=failing)
    record = receiver.run_now(KIND_SYNC)
    assert record["import"] == "error"
    assert _entry(receiver, FILE_A)["raw_backup"]["status"] == BACKUP_OK
    assert FILE_A in env.on_storage()


def test_state_written_before_this_feature_still_loads(env, tmp_path):
    base = tmp_path / "swisseldex"
    base.mkdir()
    old = rx._empty_state()
    old.pop("backup_failures")
    (base / "state.json").write_text(json.dumps(old), encoding="utf-8")
    receiver = env.receiver(tmp_path, _box())
    assert receiver.snapshot()["state_error"] == "" and receiver.snapshot()["backup_failures"] == []
