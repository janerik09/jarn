"""File-level backups to a share: archive, verify, restore, retention.

SSH is replaced by a fake that runs the same tar, sha256sum and cat commands locally, against temporary folders.
The real /etc, /root, /home and /var/tmp are never touched. Nothing here proves the commands behave the same on a
real server; that needs a test against a real managed server and a real NFS export.
"""

import asyncio
import json
import re
import subprocess
import tarfile
import time
from pathlib import Path

import pytest

from app.services import audit, backup_providers
from app.services.backup_providers import (
    BackupError, FileProvider, mapping_from_server, provider_for_mapping, restore_server_from_backup,
    server_ref, take_server_backup, validate_file_paths,
)

SERVER_A = "11111111-2222-3333-4444-555555555555"
SERVER_B = "66666666-7777-8888-9999-000000000000"


class FakeResult:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class FakeConnector:
    """Stands in for SSHConnector. Commands run locally with /var/tmp and -C / pointed at test folders."""
    sources: dict = {}
    fail_archive_with = None  # set to a return code to simulate tar failing on the server

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.connected = False
        self.connect_error = None

    async def connect(self):
        self.connected = True
        return True

    async def disconnect(self):
        self.connected = False

    def _localise(self, command: str) -> str:
        command = command.replace("sudo -S ", "").replace("sudo ", "")
        command = command.replace("/var/tmp", str(self.sources["staging"]))
        return re.sub(r" -C /(?=\s|$)", f" -C {self.sources['root']}", command)

    async def execute_command(self, command, timeout=30):
        local = self._localise(command)
        done = subprocess.run(local, shell=True, capture_output=True, text=True)
        return FakeResult(done.returncode, done.stdout, done.stderr)

    def run_stream(self, command, *, upload_path=None, download=None, timeout=3600):
        local = self._localise(command)
        if upload_path:
            done = subprocess.run(local, shell=True, stdin=open(upload_path, "rb"), capture_output=True)
            return done.returncode, done.stderr.decode()
        if "-czf -" in local and self.fail_archive_with is not None:
            return self.fail_archive_with, "tar: simulated failure"
        proc = subprocess.Popen(local, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for chunk in iter(lambda: proc.stdout.read(4096), b""):
            if download is not None:
                download(chunk)
        stderr = proc.stderr.read().decode()
        return proc.wait(), stderr


@pytest.fixture
def env(tmp_path, monkeypatch):
    source = tmp_path / "server-root"
    (source / "etc").mkdir(parents=True)
    (source / "etc" / "hostname").write_text("web1\n")
    (source / "root").mkdir()
    (source / "root" / ".bashrc").write_text("export X=1\n")
    (source / "home" / "ops").mkdir(parents=True)
    (source / "home" / "ops" / "notes.txt").write_text("keep me\n")
    staging = tmp_path / "vartmp"
    staging.mkdir()
    share = tmp_path / "share"
    share.mkdir()

    FakeConnector.sources = {"root": source, "staging": staging}
    FakeConnector.fail_archive_with = None
    monkeypatch.setattr(backup_providers, "SSHConnector", FakeConnector)

    recorded = []

    async def fake_record(event, **kwargs):
        recorded.append((event, kwargs))

    monkeypatch.setattr(audit, "record", fake_record)
    return {"source": source, "share": share, "events": recorded}


def _server(server_id, env):
    return server_ref(server_id, "web1", "10.0.0.5", 22, {"username": "ops", "password": "pw"},
                      {"backup": {"target": "nas", "type": "file"}})


def _provider(env, keep=5, require_mount=False, paths=None):
    return FileProvider(base_path=str(env["share"]), paths=paths or ["/etc", "/root", "/home"], keep=keep,
                        require_mount=require_mount)


def _run(coro):
    return asyncio.run(coro)


def test_backup_writes_verified_archive_and_manifest(env):
    provider = _provider(env)
    volid = _run(provider.backup(_server(SERVER_A, env)))

    assert volid.startswith(f"{SERVER_A}/") and volid.endswith(".tar.gz")
    archive = env["share"] / volid
    manifest = json.loads(archive.with_suffix("").with_suffix(".json").read_text())
    assert archive.stat().st_mode & 0o777 == 0o600
    assert manifest["size_bytes"] == archive.stat().st_size
    assert manifest["paths"] == ["/etc", "/root", "/home"]
    with tarfile.open(archive) as tar:
        names = tar.getnames()
    assert "etc/hostname" in names and "home/ops/notes.txt" in names
    assert not list((env["share"] / SERVER_A).glob("*.partial"))


def test_failed_tar_leaves_no_archive(env):
    FakeConnector.fail_archive_with = 2
    with pytest.raises(BackupError, match="tar exited with status 2"):
        _run(_provider(env).backup(_server(SERVER_A, env)))
    assert not list((env["share"] / SERVER_A).glob("*.tar.gz*"))
    assert _run(_provider(env).list_backups(SERVER_A)) == []


def test_restore_round_trip_puts_files_back(env):
    provider = _provider(env)
    volid = _run(provider.backup(_server(SERVER_A, env)))
    hostname = env["source"] / "etc" / "hostname"
    hostname.write_text("changed by a bad patch\n")

    _run(provider.restore(_server(SERVER_A, env), volid))

    assert hostname.read_text() == "web1\n"
    assert any(e[0] == "files.restore.complete" and e[1].get("outcome") is None for e in env["events"])


def test_restore_refuses_a_tampered_archive_and_changes_nothing(env):
    provider = _provider(env)
    volid = _run(provider.backup(_server(SERVER_A, env)))
    archive = env["share"] / volid
    manifest_path = archive.with_suffix("").with_suffix(".json")
    data = bytearray(archive.read_bytes())
    data[len(data) // 2] ^= 0xFF
    archive.write_bytes(bytes(data))
    (env["source"] / "etc" / "hostname").write_text("current\n")

    preflight = _run(provider.restore_preflight(volid, SERVER_A))
    assert not preflight["ok"]
    assert any("checksum" in e or "size" in e for e in preflight["errors"])
    with pytest.raises(BackupError, match="Restore blocked, nothing was changed"):
        _run(provider.restore(_server(SERVER_A, env), volid))
    assert (env["source"] / "etc" / "hostname").read_text() == "current\n"
    assert manifest_path.exists()


def test_restore_cannot_use_another_servers_backup(env):
    provider = _provider(env)
    volid = _run(provider.backup(_server(SERVER_A, env)))
    preflight = _run(provider.restore_preflight(volid, SERVER_B))
    assert not preflight["ok"]
    assert any("different server" in e for e in preflight["errors"])


def test_retention_keeps_only_the_newest(env):
    provider = _provider(env, keep=1)
    first = _run(provider.backup(_server(SERVER_A, env)))
    time.sleep(1.1)  # backups are named to the second
    second = _run(provider.backup(_server(SERVER_A, env)))

    listed = _run(provider.list_backups(SERVER_A))
    assert [b["volid"] for b in listed] == [second]
    assert not (env["share"] / first).exists()


def test_unmounted_share_is_refused_before_anything_is_written(env, monkeypatch):
    # The temporary folder may sit on its own mount (tmpfs), so pretend nothing is mounted
    monkeypatch.setattr(Path, "is_mount", lambda self: False)
    provider = FileProvider(base_path=str(env["share"]), require_mount=True)
    with pytest.raises(BackupError, match="not on a mounted share"):
        _run(provider.backup(_server(SERVER_A, env)))
    assert not (env["share"] / SERVER_A).exists()


def test_paths_must_be_absolute_and_never_the_whole_disk():
    assert validate_file_paths(["/etc", "/etc/", "/home"]) == ["/etc", "/home"]
    for bad in (["etc"], ["/"], ["/etc/../root"], []):
        with pytest.raises(BackupError):
            validate_file_paths(bad)


def test_mapping_kind_must_match_its_target():
    config = {"enabled": True, "targets": [
        {"id": "nas", "name": "NAS", "provider": "nfs", "base_path": "/mnt/x"},
        {"id": "pve", "name": "PVE", "provider": "proxmox", "api_url": "https://pve:8006",
         "token_id": "t", "token_secret": "s"},
    ]}
    assert isinstance(provider_for_mapping(config, {"target": "nas", "type": "file"}), FileProvider)
    with pytest.raises(BackupError, match="changed kind"):
        provider_for_mapping(config, {"target": "pve", "type": "file"})
    with pytest.raises(BackupError, match="changed kind"):
        provider_for_mapping(config, {"target": "nas", "type": "qemu", "node": "n", "vmid": 1})


def test_mapping_from_server_accepts_file_and_legacy_proxmox_mappings():
    assert mapping_from_server({"backup": {"target": "nas", "type": "file"}}) == {"target": "nas", "type": "file"}
    assert mapping_from_server({"backup": {"type": "file"}}) is None
    legacy = {"backup": {"node": "pve1", "vmid": 101, "type": "qemu"}}
    assert mapping_from_server(legacy)["vmid"] == 101
    assert mapping_from_server({"backup": {"node": "pve1", "type": "qemu"}}) is None


def test_take_and_restore_dispatch_through_the_mapping(env):
    config = {"enabled": True, "targets": [
        {"id": "nas", "name": "NAS", "provider": "nfs", "base_path": str(env["share"]),
         "paths": ["/etc"], "keep": 3, "require_mount": False},
    ]}
    server = server_ref(SERVER_A, "web1", "10.0.0.5", 22, {"username": "ops", "password": "pw"},
                        {"backup": {"target": "nas", "type": "file"}})
    volid = _run(take_server_backup(config, server))
    (env["source"] / "etc" / "hostname").write_text("changed\n")
    _run(restore_server_from_backup(config, server, volid))
    assert (env["source"] / "etc" / "hostname").read_text() == "web1\n"
    with pytest.raises(BackupError, match="does not apply"):
        _run(restore_server_from_backup(config, server, volid, target_storage="local-lvm"))


def test_file_target_config_without_folder_is_rejected():
    config = {"enabled": True, "targets": [{"id": "nas", "name": "NAS", "provider": "nfs", "base_path": ""}]}
    with pytest.raises(BackupError, match="needs a folder"):
        backup_providers.provider_from_config(config, "nas")
