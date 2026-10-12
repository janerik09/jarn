import base64
import hashlib
import os
from types import SimpleNamespace

import pytest

from app.config import settings
from app.security import DEFAULT_SECRET_KEY, require_secret_key
from app.services import offsite
from app.api.metrics import _require_metrics_token
from fastapi import HTTPException


class FakeStat:
    def __init__(self, size, mtime=0):
        self.st_size = size
        self.st_mtime = mtime


class FakeSFTP:
    def __init__(self, files=None, truncate=False):
        self.files = dict(files or {})  # path -> FakeStat
        self.truncate = truncate
        self.removed = []

    def stat(self, path):
        if path not in self.files:
            raise OSError("no such file")
        return self.files[path]

    def listdir(self, folder):
        return [p.rsplit("/", 1)[1] for p in self.files if p.rsplit("/", 1)[0] == folder]

    def put(self, local, remote):
        size = os.path.getsize(local)
        self.files[remote] = FakeStat(size // 2 if self.truncate else size)

    def posix_rename(self, a, b):
        self.files[b] = self.files.pop(a)

    def remove(self, path):
        self.removed.append(path)
        self.files.pop(path, None)


def test_fingerprint_matches_ssh_keygen_format():
    key = base64.b64encode(b"example-host-key-bytes").decode()
    line = f"ssh-ed25519 {key}"
    expected = "SHA256:" + base64.b64encode(hashlib.sha256(b"example-host-key-bytes").digest()).decode().rstrip("=")
    assert offsite.fingerprint_of(line) == expected


def test_remote_folder_must_be_absolute_and_not_root():
    for bad in ("relative/path", "/", ""):
        with pytest.raises(offsite.OffsiteError):
            offsite._remote_dir({"remote_dir": bad})
    assert offsite._remote_dir({"remote_dir": "/volume1/jarn/"}) == "/volume1/jarn"


def test_run_refuses_to_send_without_an_approved_host_key(monkeypatch):
    monkeypatch.setattr(offsite, "connect", lambda *a, **k: pytest.fail("must not connect"))
    with pytest.raises(offsite.OffsiteError, match="approve"):
        offsite.run({"host": "nas", "remote_dir": "/x", "username": "u"})


def test_prune_keeps_newest_copies_of_each_kind_and_leaves_other_files():
    folder = "/volume1/jarn"
    files = {
        f"{folder}/patching_db-20261001-000000.dump": FakeStat(1, 100),
        f"{folder}/patching_db-20261002-000000.dump": FakeStat(1, 200),
        f"{folder}/patching_db-20261003-000000.dump": FakeStat(1, 300),
        f"{folder}/audit-a-b.jsonl.gz": FakeStat(1, 50),
        f"{folder}/notes.txt": FakeStat(1, 999),
    }
    sftp = FakeSFTP(files)
    removed = offsite._prune(sftp, folder, keep=2)
    assert removed == ["patching_db-20261001-000000.dump"]
    assert f"{folder}/notes.txt" in sftp.files
    assert f"{folder}/audit-a-b.jsonl.gz" in sftp.files


def test_upload_skips_files_already_present_with_same_size(tmp_path):
    local = tmp_path / "patching_db-20261003-000000.dump"
    local.write_bytes(b"x" * 10)
    sftp = FakeSFTP({"/v/patching_db-20261003-000000.dump": FakeStat(10)})
    assert offsite._upload(sftp, local, "/v") is False


def test_upload_discards_partial_copy_on_size_mismatch(tmp_path):
    local = tmp_path / "patching_db-20261003-000000.dump"
    local.write_bytes(b"x" * 10)
    sftp = FakeSFTP(truncate=True)
    with pytest.raises(offsite.OffsiteError, match="did not match"):
        offsite._upload(sftp, local, "/v")
    assert "/v/patching_db-20261003-000000.dump.partial" in sftp.removed
    assert "/v/patching_db-20261003-000000.dump" not in sftp.files


def test_production_refuses_default_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET_KEY)
    with pytest.raises(RuntimeError):
        require_secret_key()
    monkeypatch.setattr(settings, "secret_key", "x" * 40)
    require_secret_key()  # long enough


def test_development_allows_default_secret_key(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "secret_key", DEFAULT_SECRET_KEY)
    require_secret_key()


def test_metrics_token_enforced_when_configured(monkeypatch):
    import asyncio
    monkeypatch.setattr(settings, "metrics_token", "t0ken-value")
    with pytest.raises(HTTPException):
        asyncio.run(_require_metrics_token(None))
    with pytest.raises(HTTPException):
        asyncio.run(_require_metrics_token("Bearer wrong"))
    asyncio.run(_require_metrics_token("Bearer t0ken-value"))


def test_metrics_open_on_internal_network_when_no_token_set(monkeypatch):
    import asyncio
    monkeypatch.setattr(settings, "metrics_token", None)
    asyncio.run(_require_metrics_token(None))


def test_copies_are_ordered_by_name_not_file_time():
    # Uploads in one batch share a modification time, so the order must come from the names
    names = ["patching_db-20261001-000000.dump", "patching_db-20261003-000000.dump", "patching_db-20261002-000000.dump"]
    assert offsite._newest_first(names)[0] == "patching_db-20261003-000000.dump"


def test_audit_archives_are_ordered_by_last_sequence_number():
    names = ["audit-1-100.jsonl.gz", "audit-101-900.jsonl.gz", "audit-901-950.jsonl.gz"]
    assert offsite._newest_first(names) == ["audit-901-950.jsonl.gz", "audit-101-900.jsonl.gz", "audit-1-100.jsonl.gz"]


def test_prune_removes_oldest_even_when_all_modification_times_match():
    folder = "/v"
    files = {f"{folder}/patching_db-2026100{d}-000000.dump": FakeStat(1, 500) for d in (1, 2, 3)}
    sftp = FakeSFTP(files)
    assert offsite._prune(sftp, folder, keep=2) == ["patching_db-20261001-000000.dump"]


def test_local_copies_send_only_the_retained_window(tmp_path, monkeypatch):
    monkeypatch.setattr(offsite, "BACKUP_DIR", tmp_path)
    for day in range(1, 6):
        (tmp_path / f"patching_db-202610{day:02d}-000000.dump").write_bytes(b"x")
    (tmp_path / "pre-0002-20261001-000000.dump").write_bytes(b"x")  # migration snapshot, not a scheduled dump
    chosen = [p.name for p in offsite.local_copies(keep=2)]
    assert chosen == ["patching_db-20261005-000000.dump", "patching_db-20261004-000000.dump"]
