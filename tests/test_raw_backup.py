# -*- coding: utf-8 -*-
"""Write-once raw backup of Swisseldex originals on a network storage (ADR-0010), on a simulated mount."""
from __future__ import annotations

import ast
import errno
import hashlib
import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

from shareomat.core.collector import raw_backup as rb
from shareomat.core.collector.raw_backup import (
    PROOF_SUFFIX,
    RawBackupError,
    backup_file,
    network_mounts,
    resolve_target,
    validate_subdir,
)

NAME = "20261120_155940_12X-SENDER-TEST_E66_12X-RECEIVER-TS_008.xml"
RECEIVED = datetime(2026, 11, 20, 15, 5, tzinfo=timezone.utc)


def mounts_line(source: str, point: Path, fstype: str) -> str:
    return f"{source} {str(point).replace(' ', chr(92) + '040')} {fstype} rw,relatime 0 0\n"


@pytest.fixture
def share(tmp_path):
    """A share root with a mounted CIFS storage 'omv' and a plain local folder 'local'."""
    root = tmp_path / "share"
    (root / "omv").mkdir(parents=True)
    (root / "local").mkdir()
    mounts = tmp_path / "mounts"
    mounts.write_text(
        "overlay / overlay rw 0 0\n"
        + mounts_line("systemd-1", root / "omv", "autofs")
        + mounts_line("//192.0.2.10/sicherung", root / "omv", "cifs")
        + mounts_line("/dev/sda8", root, "ext4"),
        encoding="utf-8",
    )
    return root, mounts


def target(share, subdir="swisseldex-rohdaten"):
    root, mounts = share
    return resolve_target("omv", subdir, share_root=root, mounts_file=mounts)


def source_file(tmp_path, content=b"<SDAT>original</SDAT>\n", name="source.part"):
    path = tmp_path / name
    path.write_bytes(content)
    return path, hashlib.sha256(content).hexdigest(), len(content)


def backup(share, tmp_path, content=b"<SDAT>original</SDAT>\n", name=NAME, **kw):
    src, sha, size = source_file(tmp_path, content)
    return backup_file(src, name, sha, size, kw.pop("tgt", None) or target(share), received_at=RECEIVED, **kw)


# ── mounts and target ──


def test_only_real_network_mounts_directly_below_the_share_root_are_offered(share, tmp_path):
    root, mounts = share
    with mounts.open("a", encoding="utf-8") as f:
        f.write(mounts_line("systemd-1", root / "paperless", "autofs"))
        f.write(mounts_line("//nas/deep", root / "omv" / "deeper", "cifs"))
    found = {m.name: m for m in network_mounts(root, mounts)}
    assert set(found) == {"omv", "paperless"}                     # "local" (ext4 folder) is never offered
    assert found["omv"].mounted and found["omv"].fstype == "cifs"
    assert not found["paperless"].mounted                         # automount not yet mounted


def test_a_local_folder_or_unmounted_automount_is_refused_never_used_as_fallback(share, tmp_path):
    root, mounts = share
    with pytest.raises(RawBackupError, match="nicht eingebunden"):
        resolve_target("local", "x", share_root=root, mounts_file=mounts)
    mounts.write_text(mounts_line("systemd-1", root / "omv", "autofs"), encoding="utf-8")
    with pytest.raises(RawBackupError, match="nicht eingebunden"):
        resolve_target("omv", "x", share_root=root, mounts_file=mounts)
    assert not any((root / "omv").iterdir()) and not any((root / "local").iterdir())


@pytest.mark.parametrize("bad", ["", "..", "../x", "/abs", "a/../b", "a/b/c/d", "a b", "x;rm", ".hidden"])
def test_subfolder_must_be_plain(bad):
    with pytest.raises(RawBackupError):
        validate_subdir(bad)


def test_subfolder_normalised():
    assert validate_subdir("daten/swisseldex") == "daten/swisseldex"
    assert validate_subdir(" daten\\swisseldex/ ") == "daten/swisseldex"


# ── one backup ──


def test_original_bytes_and_proof_land_in_the_month_folder(share, tmp_path):
    result = backup(share, tmp_path)
    folder = share[0] / "omv" / "swisseldex-rohdaten" / "2026-11"
    assert result.path == folder / NAME and result.proof == folder / (NAME + PROOF_SUFFIX)
    assert result.path.read_bytes() == b"<SDAT>original</SDAT>\n"
    proof = json.loads(result.proof.read_text(encoding="utf-8"))
    assert proof["sha256"] == hashlib.sha256(b"<SDAT>original</SDAT>\n").hexdigest()
    assert proof["datei"] == NAME and proof["groesse_bytes"] == 22
    assert result.relative == f"swisseldex-rohdaten/2026-11/{NAME}" and not result.reused
    assert sorted(p.name for p in folder.iterdir()) == [NAME, NAME + PROOF_SUFFIX]


def test_name_without_date_uses_the_local_month_of_reception(share, tmp_path):
    src, sha, size = source_file(tmp_path)
    late = datetime(2026, 11, 30, 23, 30, tzinfo=timezone.utc)        # 01.12. 00:30 in Zurich
    result = backup_file(src, "lieferung.xml", sha, size, target(share), received_at=late)
    assert result.path.parent.name == "2026-12"


def test_identical_file_again_reuses_the_backup_and_changes_nothing(share, tmp_path):
    first = backup(share, tmp_path)
    before = (first.path.stat().st_mtime_ns, first.proof.read_bytes())
    again = backup(share, tmp_path)
    assert again.reused and again.path == first.path
    assert (first.path.stat().st_mtime_ns, first.proof.read_bytes()) == before


def test_same_name_other_content_is_an_error_and_nothing_is_overwritten_or_renamed(share, tmp_path):
    first = backup(share, tmp_path)
    with pytest.raises(RawBackupError, match="andere Lieferung") as err:
        backup(share, tmp_path, content=b"<SDAT>other</SDAT>\n")
    assert "So lösen" in str(err.value) and "Nichts löschen" in str(err.value)
    assert first.path.read_bytes() == b"<SDAT>original</SDAT>\n"
    assert sorted(p.name for p in first.path.parent.iterdir()) == [NAME, NAME + PROOF_SUFFIX]


def test_copy_without_proof_but_identical_bytes_is_completed_after_a_restart(share, tmp_path):
    folder = share[0] / "omv" / "swisseldex-rohdaten" / "2026-11"
    folder.mkdir(parents=True)
    (folder / NAME).write_bytes(b"<SDAT>original</SDAT>\n")           # written, then Shareomat stopped
    result = backup(share, tmp_path)
    assert result.proof.exists() and result.path.read_bytes() == b"<SDAT>original</SDAT>\n"


def test_interrupted_copy_is_completed_automatically_without_overwriting(share, tmp_path, monkeypatch):
    real_write = rb._write_all

    def broken(fd, data):
        real_write(fd, data[:5])
        raise OSError(errno.EIO, "Input/output error")

    monkeypatch.setattr(rb, "_write_all", broken)
    with pytest.raises(RawBackupError, match="automatisch fort"):
        backup(share, tmp_path)
    monkeypatch.setattr(rb, "_write_all", real_write)
    data = share[0] / "omv" / "swisseldex-rohdaten" / "2026-11" / NAME
    assert data.read_bytes() == b"<SDAT" and not (data.parent / (NAME + PROOF_SUFFIX)).exists()

    opened = []
    real_open = os.open

    def spy(path, flags, *args):
        if Path(path) == data:
            opened.append(flags)
        return real_open(path, flags, *args)

    monkeypatch.setattr(rb.os, "open", spy)
    result = backup(share, tmp_path)                                   # next run: nobody had to do anything
    assert result.resumed and data.read_bytes() == b"<SDAT>original</SDAT>\n" and result.proof.exists()
    writes = [f for f in opened if f & os.O_WRONLY]
    assert any(f & os.O_APPEND for f in writes)                        # the missing bytes were appended
    assert all(not f & os.O_TRUNC and (f & os.O_APPEND or f & os.O_EXCL) for f in writes)


def test_a_foreign_file_under_the_name_is_never_touched_and_the_message_says_what_to_do(share, tmp_path):
    folder = share[0] / "omv" / "swisseldex-rohdaten" / "2026-11"
    folder.mkdir(parents=True)
    (folder / NAME).write_bytes(b"<SDAT>something completely different</SDAT>\n")
    with pytest.raises(RawBackupError) as err:
        backup(share, tmp_path)
    text = str(err.value)
    assert "So lösen" in text and "Jetzt synchronisieren" in text and "swisseldex-rohdaten/2026-11/" in text
    assert (folder / NAME).read_bytes() == b"<SDAT>something completely different</SDAT>\n"
    assert not (folder / (NAME + PROOF_SUFFIX)).exists()


def test_a_file_that_starts_differently_is_not_completed(share, tmp_path):
    folder = share[0] / "omv" / "swisseldex-rohdaten" / "2026-11"
    folder.mkdir(parents=True)
    (folder / NAME).write_bytes(b"<XML")                              # shorter, but not our beginning
    with pytest.raises(RawBackupError, match="keine gültige Sicherung"):
        backup(share, tmp_path)
    assert (folder / NAME).read_bytes() == b"<XML"


def test_storage_full_is_a_backup_error(share, tmp_path, monkeypatch):
    def full(fd, data):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(rb, "_write_all", full)
    with pytest.raises(RawBackupError, match="No space left"):
        backup(share, tmp_path)


def test_differing_checksum_after_read_back_leaves_no_proof(share, tmp_path, monkeypatch):
    real = rb._sha256_of
    omv = share[0] / "omv"

    def lying(path, limit=rb.MAX_BACKUP_BYTES):
        sha, size = real(path, limit)
        return ("0" * 64, size) if omv in Path(path).parents else (sha, size)

    monkeypatch.setattr(rb, "_sha256_of", lying)
    with pytest.raises(RawBackupError, match="weicht vom Original ab"):
        backup(share, tmp_path)
    folder = omv / "swisseldex-rohdaten" / "2026-11"
    assert not (folder / (NAME + PROOF_SUFFIX)).exists()


def test_storage_gone_between_check_and_write_writes_nothing_locally(share, tmp_path):
    tgt = target(share)
    root, mounts = share
    mounts.write_text(mounts_line("systemd-1", root / "omv", "autofs"), encoding="utf-8")   # unmounted
    with pytest.raises(RawBackupError, match="nicht mehr eingebunden"):
        backup(share, tmp_path, tgt=tgt)
    assert not any((root / "omv").iterdir())


def test_changed_source_is_refused(share, tmp_path):
    src, sha, size = source_file(tmp_path)
    src.write_bytes(b"<SDAT>changed</SDAT>\n")
    with pytest.raises(RawBackupError, match="nicht mehr dem empfangenen Original"):
        backup_file(src, NAME, sha, size, target(share), received_at=RECEIVED)


@pytest.mark.parametrize("name", ["../evil.xml", "a/b.xml", ".hidden.xml", "x.xml.sha256", ""])
def test_unsafe_names_are_refused(share, tmp_path, name):
    with pytest.raises(RawBackupError):
        backup(share, tmp_path, name=name)


def test_parallel_backups_of_the_same_file_write_it_once(share, tmp_path):
    src, sha, size = source_file(tmp_path)
    tgt = target(share)
    results, errors = [], []

    def run():
        try:
            results.append(backup_file(src, NAME, sha, size, tgt, received_at=RECEIVED))
        except RawBackupError as exc:     # pragma: no cover - must not happen
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors and len(results) == 4 and sum(not r.reused for r in results) == 1
    assert sorted(p.name for p in results[0].path.parent.iterdir()) == [NAME, NAME + PROOF_SUFFIX]


# ── the module itself never replaces, renames, truncates or deletes ──


def test_module_has_no_overwriting_or_deleting_calls():
    source = Path(rb.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden_attrs = {"rename", "unlink", "rmdir", "rmtree", "remove", "move", "truncate", "ftruncate",
                       "write_text", "write_bytes", "touch"}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
            found.append(node.attr)
        if isinstance(node, ast.Attribute) and node.attr == "replace" and isinstance(node.value, ast.Name) \
                and node.value.id == "os":
            found.append("os.replace")
        if isinstance(node, ast.Name) and node.id == "shutil":
            found.append("shutil")
    assert found == []
    assert "O_TRUNC" not in source and "O_EXCL" in source


# ── storage test (button "Speicher testen") ──


def test_storage_check_proves_write_read_and_no_overwrite(share):
    check = rb.check_storage(target(share))
    labels = [s.label for s in check.steps]
    assert check.ok, check.steps
    assert labels[:5] == ["Netzwerkspeicher eingebunden", "Ordner anlegen", "Neue Datei schreiben",
                          "Zurücklesen und SHA-256 vergleichen", "Überschreiben wird verhindert"]
    test_file = share[0] / "omv" / check.test_file
    assert test_file.read_text(encoding="utf-8").startswith("Shareomat Speichertest")
    assert check.test_file.startswith("swisseldex-rohdaten/verbindungstest/test-")


def test_storage_check_fails_when_overwriting_is_possible(share, monkeypatch):
    calls = {"n": 0}
    real = rb._create_exclusive

    def lax(path):
        calls["n"] += 1
        if calls["n"] == 2:                                            # the storage ignores O_EXCL
            return os.open(path, os.O_WRONLY | getattr(os, "O_BINARY", 0))
        return real(path)

    monkeypatch.setattr(rb, "_create_exclusive", lax)
    check = rb.check_storage(target(share))
    assert not check.ok and check.steps[-1].label == "Überschreiben wird verhindert" and not check.steps[-1].ok
    assert (share[0] / "omv" / check.test_file).read_text(encoding="utf-8").startswith("Shareomat")


def test_storage_check_fails_when_reading_back_gives_other_bytes(share, monkeypatch):
    real = rb._sha256_of

    def lying(path, limit=rb.MAX_BACKUP_BYTES):
        sha, size = real(path, limit)
        return "0" * 64, size

    monkeypatch.setattr(rb, "_sha256_of", lying)
    check = rb.check_storage(target(share))
    assert not check.ok and check.steps[-1].label == "Zurücklesen und SHA-256 vergleichen"


def test_storage_check_stops_when_the_storage_is_gone(share):
    tgt = target(share)
    root, mounts = share
    mounts.write_text(mounts_line("systemd-1", root / "omv", "autofs"), encoding="utf-8")
    check = rb.check_storage(tgt)
    assert not check.ok and [s.ok for s in check.steps] == [True, False] and not any((root / "omv").iterdir())


def test_files_are_created_exclusively(share, tmp_path, monkeypatch):
    flags_seen = []
    real_open = os.open

    def spy(path, flags, *args):
        if Path(path).parent.name == "2026-11":
            flags_seen.append(flags)
        return real_open(path, flags, *args)

    monkeypatch.setattr(rb.os, "open", spy)
    backup(share, tmp_path)
    writes = [f for f in flags_seen if f & os.O_WRONLY]
    assert len(writes) == 2 and all(f & os.O_EXCL and f & os.O_CREAT and not f & os.O_TRUNC for f in writes)
