import asyncio
import base64
import struct

from app.connectors.ssh import SSHConnector, host_key_fingerprint, pinned_host_key


def _ed25519_line(raw: bytes = b"\x01" * 32) -> str:
    blob = struct.pack(">I", 11) + b"ssh-ed25519" + struct.pack(">I", len(raw)) + raw
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


def test_fingerprint_matches_ssh_keygen_style():
    line = _ed25519_line()
    digest = __import__("hashlib").sha256(base64.b64decode(line.split()[1])).digest()
    assert host_key_fingerprint(line) == "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def test_pinned_key_read_from_server_metadata_only():
    assert pinned_host_key({"ssh_host_key": _ed25519_line()}) == _ed25519_line()
    assert pinned_host_key({}) is None
    assert pinned_host_key(None) is None


def test_connector_refuses_to_connect_without_an_approved_key():
    conn = SSHConnector(hostname="192.0.2.10", username="ops", password="never-sent", port=22)
    assert conn._connect_sync() is False
    assert "No SSH host key is approved" in conn.connect_error


def test_connector_refuses_when_approved_key_type_is_unknown():
    conn = SSHConnector(hostname="192.0.2.10", username="ops", password="never-sent", host_key="ssh-bogus AAAA")
    assert conn._connect_sync() is False
    assert "Unsupported approved host key type" in conn.connect_error


def test_discovery_sends_no_credentials_to_an_unapproved_host(monkeypatch):
    from app.services import bulk_discovery

    key = _ed25519_line()
    monkeypatch.setattr(bulk_discovery, "_port_open", _async(True))
    monkeypatch.setattr(bulk_discovery, "fetch_host_key", lambda ip, port: key)

    def must_not_login(*args, **kwargs):
        raise AssertionError("credentials must not be used before the key is approved")

    monkeypatch.setattr(bulk_discovery, "_inspect_host_sync", must_not_login)
    result = asyncio.run(bulk_discovery._discover_one(
        "192.0.2.10", 22, "ops", "secret", None, asyncio.Semaphore(1), approved={}))
    assert result["status"] == "host_key_unapproved"
    assert result["host_key_fingerprint"] == host_key_fingerprint(key)


def test_discovery_logs_in_only_with_the_approved_fingerprint(monkeypatch):
    from app.services import bulk_discovery

    key = _ed25519_line()
    seen = {}
    monkeypatch.setattr(bulk_discovery, "_port_open", _async(True))
    monkeypatch.setattr(bulk_discovery, "fetch_host_key", lambda ip, port: key)
    monkeypatch.setattr(bulk_discovery, "_inspect_host_sync",
                        lambda ip, port, user, pw, pk, host_key: seen.setdefault("key", host_key) and {"status": "linux", "os_type": "debian"})
    monkeypatch.setattr(bulk_discovery, "_reverse_dns", _async(None))
    result = asyncio.run(bulk_discovery._discover_one(
        "192.0.2.10", 22, "ops", "secret", None, asyncio.Semaphore(1),
        approved={"192.0.2.10": host_key_fingerprint(key)}))
    assert result["status"] == "linux"
    assert seen["key"] == key


def _async(value):
    async def inner(*args, **kwargs):
        return value
    return inner
