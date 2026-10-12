"""
Off-host copy of the database dumps and audit archives, over SFTP.

The copy is off until an administrator turns it on. Each run uploads any local dump or audit archive the remote
folder does not have yet, checks the uploaded size, and only then removes the oldest remote copies beyond the
number to keep. Connections are pinned to the server's SSH host key, so a different server cannot receive the
backups without the administrator re-approving it.
"""

import base64
import hashlib
import io
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/backups"))
DUMP_PATTERN = re.compile(r"^patching_db-\d{8}-\d{6}\.dump$")
AUDIT_PATTERN = re.compile(r"^audit-.+\.jsonl\.gz$")
CONNECT_TIMEOUT = 20
MIN_KEEP = 1
MAX_KEEP = 365


class OffsiteError(Exception):
    pass


def _key_classes():
    import paramiko
    return {
        "ssh-ed25519": paramiko.Ed25519Key,
        "ssh-rsa": paramiko.RSAKey,
        "ecdsa-sha2-nistp256": paramiko.ECDSAKey,
        "ecdsa-sha2-nistp384": paramiko.ECDSAKey,
        "ecdsa-sha2-nistp521": paramiko.ECDSAKey,
    }


def _load_private_key(pem: str):
    import paramiko
    last_error: Optional[Exception] = None
    for cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return cls.from_private_key(io.StringIO(pem))
        except paramiko.SSHException as e:
            last_error = e
    raise OffsiteError(f"The SSH private key could not be read (passphrase-protected keys are not supported): {last_error}")


def fingerprint_of(host_key_line: str) -> str:
    """SHA256 fingerprint in the same form as ssh-keygen prints it, for comparing with the NAS's own display."""
    _, b64 = host_key_line.split()[:2]
    digest = hashlib.sha256(base64.b64decode(b64)).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


class _CaptureHostKey:
    """Used only when no host key is pinned yet. Records the key the server offers so the administrator can approve it."""

    def __init__(self) -> None:
        self.line: Optional[str] = None

    def missing_host_key(self, client, hostname, key) -> None:
        self.line = f"{key.get_name()} {key.get_base64()}"


def connect(cfg: Dict[str, Any], pin_host_key: bool = True):
    """Opens an SSH connection to the configured server. Returns (client, sftp, host_key_line)."""
    import paramiko
    host = cfg.get("host")
    if not host:
        raise OffsiteError("No host is set for the off-host copy")
    client = paramiko.SSHClient()
    pinned = cfg.get("host_key")
    capture = _CaptureHostKey()
    if pinned and pin_host_key:
        keytype, b64 = pinned.split()[:2]
        cls = _key_classes().get(keytype)
        if cls is None:
            raise OffsiteError(f"Unsupported pinned host key type: {keytype}")
        client.get_host_keys().add(host, keytype, cls(data=base64.b64decode(b64)))
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        client.set_missing_host_key_policy(capture)
    kwargs: Dict[str, Any] = {"hostname": host, "port": int(cfg.get("port") or 22),
                              "username": cfg.get("username"), "timeout": CONNECT_TIMEOUT,
                              "allow_agent": False, "look_for_keys": False}
    if cfg.get("auth") == "password":
        kwargs["password"] = cfg.get("password") or ""
    else:
        kwargs["pkey"] = _load_private_key(cfg.get("private_key") or "")
    try:
        client.connect(**kwargs)
    except paramiko.BadHostKeyException as e:
        client.close()
        raise OffsiteError(f"The server's host key changed since it was approved. Test the connection again and approve the new key only if you expected the change: {e}")
    except paramiko.AuthenticationException:
        client.close()
        raise OffsiteError("The server rejected the username or key/password")
    except (paramiko.SSHException, OSError) as e:
        client.close()
        raise OffsiteError(f"Could not connect to {host}: {e}")
    line = capture.line if capture.line else None
    return client, client.open_sftp(), line


def test(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Connects, checks the remote folder exists and is writable, and reports the server's host key. Writes nothing."""
    client, sftp, seen_line = connect(cfg)
    try:
        remote_dir = _remote_dir(cfg)
        sftp.stat(remote_dir)
        probe = f"{remote_dir}/.jarn-write-test"
        sftp.putfo(io.BytesIO(b"ok"), probe)
        sftp.remove(probe)
        names = sftp.listdir(remote_dir)
        line = seen_line or cfg.get("host_key")
        return {
            "ok": True,
            "host_key": line,
            "host_key_fingerprint": fingerprint_of(line) if line else None,
            "host_key_pinned": bool(cfg.get("host_key")) and line == cfg.get("host_key"),
            "remote_dump_count": sum(1 for n in names if DUMP_PATTERN.match(n)),
            "remote_audit_count": sum(1 for n in names if AUDIT_PATTERN.match(n)),
        }
    except OSError as e:
        raise OffsiteError(f"The remote folder {cfg.get('remote_dir')} is missing or not writable: {e}")
    finally:
        sftp.close()
        client.close()


def _remote_dir(cfg: Dict[str, Any]) -> str:
    remote = (cfg.get("remote_dir") or "").rstrip("/")
    if not remote.startswith("/") or remote in ("", "/"):
        raise OffsiteError("The remote folder must be an absolute path, not the root folder")
    return remote


def _newest_first(names: List[str]) -> List[str]:
    """Order copies newest first. Dumps carry a UTC timestamp in their name. Audit archives are named by their
    first and last event sequence numbers, so the last number gives the order. File times are not used: a batch
    of uploads all gets the same time."""
    def key(name: str):
        if DUMP_PATTERN.match(name):
            return (1, name)
        match = AUDIT_PATTERN.match(name)
        last = re.match(r"^audit-\d+-(\d+)\.jsonl\.gz$", name)
        return (0, int(last.group(1)) if last else 0)
    return sorted(names, key=key, reverse=True)


def local_copies(keep: int) -> List[Path]:
    """The newest `keep` dumps and `keep` audit archives. Older local files are not sent: they would be pruned again."""
    dumps = [p for p in BACKUP_DIR.glob("patching_db-*.dump") if DUMP_PATTERN.match(p.name)]
    audit_dir = BACKUP_DIR / "audit"
    audits = [p for p in audit_dir.glob("audit-*.jsonl.gz") if AUDIT_PATTERN.match(p.name)] if audit_dir.is_dir() else []
    chosen = []
    for group in (dumps, audits):
        by_name = {p.name: p for p in group}
        chosen += [by_name[name] for name in _newest_first(list(by_name))[:keep]]
    return chosen


def _upload(sftp, local: Path, remote_dir: str) -> bool:
    """Copies one file under a temporary name, checks the size, then renames it into place. False if already there."""
    final = f"{remote_dir}/{local.name}"
    size = local.stat().st_size
    try:
        if sftp.stat(final).st_size == size:
            return False
    except OSError:
        pass
    partial = final + ".partial"
    sftp.put(str(local), partial)
    if sftp.stat(partial).st_size != size:
        sftp.remove(partial)
        raise OffsiteError(f"Upload of {local.name} did not match the local size; it was discarded")
    try:
        sftp.posix_rename(partial, final)
    except (AttributeError, OSError):
        try:
            sftp.remove(final)
        except OSError:
            pass
        sftp.rename(partial, final)
    return True


def _prune(sftp, remote_dir: str, keep: int) -> List[str]:
    """Removes the oldest remote copies of each kind, keeping the newest `keep`. Only files named like ours are touched."""
    removed = []
    names = [n for n in sftp.listdir(remote_dir) if DUMP_PATTERN.match(n) or AUDIT_PATTERN.match(n)]
    for pattern in (DUMP_PATTERN, AUDIT_PATTERN):
        group = _newest_first([n for n in names if pattern.match(n)])
        for old in group[keep:]:
            sftp.remove(f"{remote_dir}/{old}")
            removed.append(old)
    return removed


def run(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Uploads what is missing, then prunes. Pruning only happens when every upload succeeded."""
    if not cfg.get("host_key"):
        raise OffsiteError("Test the connection and approve the server's host key before copies are sent")
    keep = max(MIN_KEEP, min(MAX_KEEP, int(cfg.get("keep_copies") or 14)))
    client, sftp, seen_line = connect(cfg)
    uploaded: List[str] = []
    try:
        remote_dir = _remote_dir(cfg)
        sftp.stat(remote_dir)
        for path in local_copies(keep):
            if _upload(sftp, path, remote_dir):
                uploaded.append(path.name)
                logger.info("offsite_uploaded", file=path.name, bytes=path.stat().st_size)
        removed = _prune(sftp, remote_dir, keep)
    finally:
        sftp.close()
        client.close()
    return {"uploaded": uploaded, "removed": removed, "keep_copies": keep, "host": cfg.get("host")}
