"""
Backup providers for pre-patch backups and restores.

Two kinds of backup source:

- Proxmox VE (with a Proxmox Backup Server storage) backs up whole VMs and containers. Each managed server maps to
  one through metadata_["backup"] = {"target", "node", "vmid", "type"}.
- A file target (NFS share) backs up a server's configured paths as a tar.gz over SSH, and stores it in a folder
  on this host, normally a mounted NFS export. Mapping: metadata_["backup"] = {"target", "type": "file"}.
"""

import asyncio
import gzip
import hashlib
import json
import os
import re
import shlex
import uuid
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.connectors.ssh import SSHConnector, pinned_host_key
from app.logging_config import get_logger

logger = get_logger(__name__)

TASK_POLL_SECONDS = 10
# Free space needed on a storage for a disk: the disk size plus 10% headroom for metadata and thin-provisioning growth
RESTORE_HEADROOM = 1.10
_QEMU_DISK = re.compile(r"^(ide|sata|scsi|virtio|efidisk|tpmstate)\d+$")
_LXC_DISK = re.compile(r"^(rootfs|mp\d+)$")
_SIZE = re.compile(r"^(\d+(?:\.\d+)?)([KMGT]?)$", re.IGNORECASE)
_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}


class BackupError(Exception):
    pass


def parse_size(text: str) -> Optional[int]:
    """'32G' -> bytes. Proxmox writes disk sizes with K, M, G or T suffixes."""
    match = _SIZE.match(text.strip())
    if not match:
        return None
    return int(float(match.group(1)) * _UNITS[match.group(2).upper()])


def parse_disks(items: List[Tuple[str, str]], vtype: str) -> List[Dict[str, Any]]:
    """Disks from a guest config as (key, value) pairs. CD-ROMs, removable media and host files are skipped."""
    pattern = _QEMU_DISK if vtype == "qemu" else _LXC_DISK
    disks = []
    for key, value in items:
        if not pattern.match(key):
            continue
        value = str(value)
        if "media=cdrom" in value or value.startswith("none"):
            continue
        first, _, options = value.partition(",")
        if ":" not in first:
            continue  # a bare host path, not a storage volume
        storage, _, volume = first.partition(":")
        size = None
        for option in options.split(","):
            if option.startswith("size="):
                size = parse_size(option[5:])
        disks.append({"key": key, "storage": storage, "volume": volume, "size_bytes": size or 0})
    return disks


def _config_items(config: Any) -> List[Tuple[str, str]]:
    """A config from the API (dict) or a backup's extracted config (text, 'key: value' lines)."""
    if isinstance(config, dict):
        return [(k, str(v)) for k, v in config.items()]
    items = []
    for line in str(config or "").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            items.append((key.strip(), value.strip()))
    return items


def _job_covers(job: Dict[str, Any], node: str, vmid: int) -> bool:
    if str(job.get("enabled", 1)) == "0":
        return False
    if job.get("node") and job["node"] != node:
        return False
    if str(job.get("all", 0)) in ("1", "True", "true"):
        excluded = {int(x) for x in str(job.get("exclude", "")).split(",") if x.strip().isdigit()}
        return vmid not in excluded
    return vmid in {int(x) for x in str(job.get("vmid", "")).split(",") if x.strip().isdigit()}


class ProxmoxProvider:
    name = "proxmox"

    def __init__(self, api_url: str, token_id: str, token_secret: str, storage: Optional[str] = None, verify_tls: bool = False):
        self.base = api_url.rstrip("/") + "/api2/json"
        self.headers = {"Authorization": f"PVEAPIToken={token_id}={token_secret}"}
        self.storage = storage
        self.verify_tls = verify_tls

    async def _request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None,
                       params: Optional[Dict[str, Any]] = None) -> Any:
        try:
            async with httpx.AsyncClient(verify=self.verify_tls, timeout=60, headers=self.headers) as client:
                response = await client.request(method, self.base + path, data=data, params=params)
        except httpx.HTTPError as e:
            raise BackupError(f"Proxmox API unreachable: {e}")
        if response.status_code >= 400:
            raise BackupError(f"Proxmox API {response.status_code} on {path}: {response.text[:300]}")
        return response.json()["data"]

    async def test(self) -> Dict[str, Any]:
        version = await self._request("GET", "/version")
        if not self.storage:
            # No fallback configured: each VM's storage comes from its Datacenter backup job
            return {"pve_version": version.get("version"), "storage": None, "storage_type": None}
        storage = await self._request("GET", f"/storage/{self.storage}")
        if "backup" not in str(storage.get("content", "")):
            raise BackupError(f"Storage '{self.storage}' does not accept backups")
        return {"pve_version": version.get("version"), "storage": self.storage, "storage_type": storage.get("type")}

    async def guest_os(self, node: str, vtype: str, vmid: int) -> str:
        """windows, linux or other, from the guest's configured OS type. Containers are Linux."""
        if vtype == "lxc":
            return "linux"
        config = await self._request("GET", f"/nodes/{node}/qemu/{vmid}/config")
        ostype = str(config.get("ostype", ""))
        if ostype.startswith("w"):
            return "windows"
        if ostype.startswith("l"):
            return "linux"
        return "other"

    async def list_guests(self) -> List[Dict[str, Any]]:
        resources = await self._request("GET", "/cluster/resources", params={"type": "vm"})
        jobs = await self._request("GET", "/cluster/backup")
        guests = [
            {"vmid": r["vmid"], "node": r["node"], "type": r["type"], "name": r.get("name", ""), "status": r.get("status")}
            for r in resources if r.get("type") in ("qemu", "lxc")
        ]
        for g in guests:
            storages = sorted({j["storage"] for j in jobs if j.get("storage") and _job_covers(j, g["node"], int(g["vmid"]))})
            g["backup_storages"] = storages
            g["os"] = await self.guest_os(g["node"], g["type"], int(g["vmid"]))
        return guests

    async def wait_task(self, node: str, upid: str, timeout: int = 6 * 3600) -> None:
        waited = 0
        while True:
            status = await self._request("GET", f"/nodes/{node}/tasks/{upid}/status")
            if status.get("status") == "stopped":
                if status.get("exitstatus") == "OK":
                    return
                raise BackupError(f"Task failed: {status.get('exitstatus')}")
            if waited >= timeout:
                raise BackupError("Timed out waiting for Proxmox task")
            await asyncio.sleep(TASK_POLL_SECONDS)
            waited += TASK_POLL_SECONDS

    async def resolve_storage(self, node: str, vmid: int) -> str:
        """Storage of the Datacenter backup job that covers this guest, else the target's fallback storage."""
        jobs = await self._request("GET", "/cluster/backup")
        storages = {j["storage"] for j in jobs if j.get("storage") and _job_covers(j, node, vmid)}
        if len(storages) > 1:
            raise BackupError(f"VM {vmid} is in Datacenter backup jobs writing to different storages: {', '.join(sorted(storages))}")
        if storages:
            return storages.pop()
        if self.storage:
            return self.storage
        raise BackupError(
            f"VM {vmid} is not in any Datacenter backup job. If it is a Windows VM it is out of scope for this tool; "
            "if it is a Linux server, add it to inventory and to a backup job"
        )

    async def backup(self, node: str, vmid: int) -> str:
        from app.services import audit
        storage = await self.resolve_storage(node, vmid)
        await audit.record("proxmox.backup.start", category="backup", resource_type="vm",
                           details={"node": node, "vmid": vmid, "storage": storage})
        try:
            upid = await self._request("POST", f"/nodes/{node}/vzdump", data={
                "vmid": vmid,
                "storage": storage,
                "mode": "snapshot",
                "compress": "zstd",
                "remove": 0,
            })
            await self.wait_task(node, upid)
            backups = await self.list_backups(node, vmid, storage)
            if not backups:
                raise BackupError("Backup finished but no backup was found on storage")
        except BackupError as e:
            await audit.record("proxmox.backup.complete", category="backup", outcome="failure", resource_type="vm",
                               reason=str(e), details={"node": node, "vmid": vmid, "storage": storage})
            raise
        await audit.record("proxmox.backup.complete", category="backup", resource_type="vm",
                           details={"node": node, "vmid": vmid, "storage": storage, "volid": backups[0]["volid"]})
        return backups[0]["volid"]

    async def list_backups(self, node: str, vmid: int, storage: Optional[str] = None) -> List[Dict[str, Any]]:
        storage = storage or await self.resolve_storage(node, vmid)
        content = await self._request("GET", f"/nodes/{node}/storage/{storage}/content",
                                      params={"content": "backup", "vmid": vmid})
        backups = [{"volid": c["volid"], "ctime": c.get("ctime"), "size": c.get("size")} for c in content]
        return sorted(backups, key=lambda b: b["ctime"] or 0, reverse=True)

    async def stop(self, node: str, vtype: str, vmid: int) -> None:
        from app.services import audit
        try:
            upid = await self._request("POST", f"/nodes/{node}/{vtype}/{vmid}/status/stop")
            await self.wait_task(node, upid)
        except BackupError as e:
            await audit.record("proxmox.vm.stop", category="proxmox", outcome="failure", resource_type="vm",
                               reason=str(e), details={"node": node, "vmid": vmid, "type": vtype, "api": self.base})
            raise
        await audit.record("proxmox.vm.stop", category="proxmox", resource_type="vm",
                           details={"node": node, "vmid": vmid, "type": vtype, "api": self.base})

    async def start(self, node: str, vtype: str, vmid: int) -> None:
        from app.services import audit
        try:
            upid = await self._request("POST", f"/nodes/{node}/{vtype}/{vmid}/status/start")
            await self.wait_task(node, upid)
        except BackupError as e:
            await audit.record("proxmox.vm.start", category="proxmox", outcome="failure", resource_type="vm",
                               reason=str(e), details={"node": node, "vmid": vmid, "type": vtype})
            raise
        await audit.record("proxmox.vm.start", category="proxmox", resource_type="vm",
                           details={"node": node, "vmid": vmid, "type": vtype})

    async def node_storages(self, node: str) -> Dict[str, Dict[str, Any]]:
        """Storages visible on a node, keyed by storage id, with their state and free space."""
        rows = await self._request("GET", f"/nodes/{node}/storage")
        return {s["storage"]: s for s in rows}

    async def restore_preflight(self, node: str, vtype: str, vmid: int, volid: str,
                                target_storage: Optional[str] = None) -> Dict[str, Any]:
        """Check a restore before anything is stopped or overwritten.

        By default each disk goes back to the storage it came from. With target_storage every disk goes to that
        storage instead. Space already used by the current VM's disks on a storage is counted as free, because
        Proxmox removes them when it restores over the VM.
        """
        if not re.fullmatch(r"[A-Za-z0-9_.-]+:backup/[^\s]+", volid or ""):
            raise BackupError("That is not a backup identifier (expected storage:backup/...)")
        checks: List[Dict[str, Any]] = []
        errors: List[str] = []
        warnings: List[str] = []
        result: Dict[str, Any] = {
            "volid": volid, "node": node, "vmid": vmid, "type": vtype, "target_storage": target_storage,
            "disks": [], "storages": [], "candidates": [], "checks": checks, "errors": errors, "warnings": warnings,
        }

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})
            if not ok:
                errors.append(detail)

        backup_storage = volid.split(":", 1)[0]
        try:
            backups = await self._request("GET", f"/nodes/{node}/storage/{backup_storage}/content",
                                          params={"content": "backup", "vmid": vmid})
        except BackupError as e:
            check("backup_present", False, f"Could not list backups on {backup_storage}: {e}")
            result["ok"] = False
            return result
        found = next((b for b in backups if b.get("volid") == volid), None)
        check("backup_present", found is not None,
              f"Backup {volid} is on {backup_storage}" if found else f"Backup {volid} was not found on {backup_storage}")
        if found:
            result["backup_size"] = found.get("size")

        try:
            extracted = await self._request("GET", f"/nodes/{node}/vzdump/extractconfig", params={"volume": volid})
        except BackupError as e:
            check("backup_readable", False, f"Could not read the configuration stored in the backup: {e}")
            result["ok"] = False
            return result
        check("backup_readable", True, "The configuration stored in the backup can be read")
        disks = parse_disks(_config_items(extracted), vtype)
        check("backup_has_disks", bool(disks),
              f"The backup contains {len(disks)} disk(s) to restore" if disks else "The backup contains no disks to restore")
        if not disks:
            result["ok"] = False
            return result

        try:
            current = parse_disks(_config_items(await self._request("GET", f"/nodes/{node}/{vtype}/{vmid}/config")), vtype)
        except BackupError:
            current = []  # the VM no longer exists, so nothing is freed
        freed: Dict[str, int] = {}
        for disk in current:
            freed[disk["storage"]] = freed.get(disk["storage"], 0) + disk["size_bytes"]

        storages = await self.node_storages(node)
        needed_content = "images" if vtype == "qemu" else "rootdir"
        need: Dict[str, int] = {}
        for disk in disks:
            dest = target_storage or disk["storage"]
            disk["target_storage"] = dest
            need[dest] = need.get(dest, 0) + int(disk["size_bytes"] * RESTORE_HEADROOM)
        result["disks"] = disks

        for dest, required in sorted(need.items()):
            info = storages.get(dest)
            problems = []
            if info is None:
                problems.append("storage does not exist on this node")
            else:
                if str(info.get("enabled", 1)) == "0":
                    problems.append("storage is disabled")
                if not info.get("active"):
                    problems.append("storage is not active")
                if needed_content not in str(info.get("content", "")).split(","):
                    problems.append(f"storage does not accept {needed_content}")
            available = int((info or {}).get("avail", 0)) + freed.get(dest, 0)
            if info is not None and available < required:
                problems.append(f"needs {required} bytes free, has {available}")
            ok = not problems
            check(f"storage:{dest}", ok,
                  f"Storage {dest}: " + "; ".join(problems) if problems else f"Storage {dest} has room and is usable")
            try:
                perms = await self._request("GET", "/access/permissions", params={"path": f"/storage/{dest}"})
                granted = perms.get(f"/storage/{dest}", {}) if isinstance(perms, dict) else {}
                if not granted.get("Datastore.AllocateSpace"):
                    check(f"permission:{dest}", False,
                          f"The API token lacks Datastore.AllocateSpace on /storage/{dest}")
            except BackupError as e:
                warnings.append(f"Could not verify permissions on {dest}: {e}")
            result["storages"].append({
                "storage": dest, "required_bytes": required, "available_bytes": available, "ok": ok,
                "problems": problems,
            })

        # Storages that could hold the whole VM if every disk went there, for the storage picker
        whole_vm = sum(int(d["size_bytes"] * RESTORE_HEADROOM) for d in disks)
        for storage_id, info in sorted(storages.items()):
            if needed_content not in str(info.get("content", "")).split(","):
                continue
            if str(info.get("enabled", 1)) == "0" or not info.get("active"):
                continue
            available = int(info.get("avail", 0)) + freed.get(storage_id, 0)
            result["candidates"].append({
                "storage": storage_id, "type": info.get("type"), "available_bytes": available,
                "total_bytes": int(info.get("total", 0)), "fits": available >= whole_vm,
            })
        result["ok"] = not errors
        return result

    async def restore(self, node: str, vtype: str, vmid: int, volid: str, target_storage: Optional[str] = None) -> None:
        from app.services import audit
        await audit.record("proxmox.restore.start", category="backup", resource_type="vm",
                           details={"node": node, "vmid": vmid, "type": vtype, "volid": volid,
                                    "target_storage": target_storage})
        # Everything is checked before the VM is stopped, so a restore that cannot succeed never touches the VM
        preflight = await self.restore_preflight(node, vtype, vmid, volid, target_storage)
        await audit.record("proxmox.restore.preflight", category="backup",
                           outcome="success" if preflight["ok"] else "failure", resource_type="vm",
                           reason="; ".join(preflight["errors"]) or None,
                           details={"node": node, "vmid": vmid, "volid": volid, "target_storage": target_storage,
                                    "disks": preflight["disks"], "storages": preflight["storages"]})
        if not preflight["ok"]:
            raise BackupError("Restore blocked, nothing was changed: " + "; ".join(preflight["errors"]))
        status = await self._request("GET", f"/nodes/{node}/{vtype}/{vmid}/status/current")
        if status.get("status") == "running":
            await self.stop(node, vtype, vmid)
        data: Dict[str, Any] = {"vmid": vmid, "force": 1}
        if target_storage:
            data["storage"] = target_storage
        if vtype == "qemu":
            upid = await self._request("POST", f"/nodes/{node}/qemu", data={**data, "archive": volid})
        else:
            upid = await self._request("POST", f"/nodes/{node}/lxc", data={**data, "ostemplate": volid, "restore": 1})
        try:
            await self.wait_task(node, upid)
        except BackupError as e:
            # Proxmox may have part-written the disks, so the VM is not started again automatically
            await audit.record("proxmox.restore.complete", category="backup", outcome="failure", resource_type="vm",
                               reason=str(e), details={"node": node, "vmid": vmid, "volid": volid, "vm_left_stopped": True})
            raise BackupError(
                f"Restore of VM {vmid} failed and the VM was left stopped. Proxmox may have part-written its disks, "
                f"so check the disks before starting it. Cause: {e}"
            )
        await self.start(node, vtype, vmid)
        await audit.record("proxmox.restore.complete", category="backup", resource_type="vm",
                           details={"node": node, "vmid": vmid, "volid": volid})


FILE_DEFAULT_PATHS = ["/etc", "/boot", "/root", "/home", "/srv", "/opt", "/usr/local"]
FILE_DEFAULT_KEEP = 5
FILE_MAX_KEEP = 365
FILE_TRANSFER_TIMEOUT = 6 * 3600
FILE_RESTORE_WARNING = (
    "Only the backed-up paths are restored. Packages, the kernel and anything outside those paths are not rolled back, "
    "and files created after the backup are left in place."
)
_SERVER_ID = re.compile(r"^[0-9a-fA-F-]{36}$")
_FILE_VOLID = re.compile(r"^([0-9a-fA-F-]{36})/(\d{8}-\d{6})\.tar\.gz$")


def validate_file_paths(paths: List[str]) -> List[str]:
    """Absolute paths to back up. The root filesystem itself is refused, so a backup cannot silently cover the whole disk."""
    cleaned = []
    for raw in paths:
        path = (raw or "").strip()
        if not path:
            continue
        if not path.startswith("/") or ".." in path.split("/") or any(c in path for c in "\n\r\0"):
            raise BackupError(f"Backup path '{path}' must be an absolute path without '..'")
        normalized = "/" + "/".join(p for p in path.split("/") if p)
        if normalized == "/":
            raise BackupError("The whole filesystem cannot be a backup path; list the folders to back up instead")
        if normalized not in cleaned:
            cleaned.append(normalized)
    if not cleaned:
        raise BackupError("At least one path to back up is required")
    return cleaned


def _epoch(created_at: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(created_at).timestamp() if created_at else None
    except ValueError:
        return None


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class FileProvider:
    """Backs up a server's paths over SSH into a folder on this host, normally an NFS share mounted there.

    Layout: <base>/<server id>/<YYYYmmdd-HHMMSS>.tar.gz with a .json manifest beside it. The manifest holds the
    size and SHA-256 that restores check against. Nothing is installed on the managed server beyond tar, which the
    server already has.
    """

    name = "nfs"
    kind = "files"

    def __init__(self, base_path: str, paths: Optional[List[str]] = None, keep: int = FILE_DEFAULT_KEEP,
                 require_mount: bool = True):
        self.base_path = base_path
        self.paths = validate_file_paths(list(paths)) if paths else list(FILE_DEFAULT_PATHS)
        self.keep = int(keep or FILE_DEFAULT_KEEP)
        self.require_mount = bool(require_mount)

    def root(self) -> Path:
        base = Path(self.base_path)
        if not base.is_dir():
            raise BackupError(f"Backup folder {base} does not exist or is not a folder on this host")
        if self.require_mount:
            # Without this check a missing share would make the backup fill the local disk instead of failing
            point = base
            while not point.is_mount() and point != point.parent:
                point = point.parent
            if not point.is_mount() or point == Path("/"):
                raise BackupError(
                    f"{base} is not on a mounted share. Mount the NFS export there, or turn off "
                    "'Require the share to be mounted' for a local folder"
                )
        return base

    def _server_dir(self, server_id: str) -> Path:
        if not _SERVER_ID.match(str(server_id or "")):
            raise BackupError("Invalid server id")
        return self.root() / str(server_id)

    async def test(self) -> Dict[str, Any]:
        root = self.root()
        probe = root / f".jarn-write-test-{uuid.uuid4().hex}"
        try:
            probe.write_bytes(b"jarn")
        except OSError as e:
            raise BackupError(f"Cannot write to {root}: {e}")
        finally:
            probe.unlink(missing_ok=True)
        return {"provider": self.name, "base_path": str(root), "keep": self.keep, "paths": self.paths}

    async def backup(self, server: Dict[str, Any]) -> str:
        from app.services import audit
        server_id, ssh = server["id"], server["ssh"]
        server_dir = self._server_dir(server_id)
        server_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(server_dir, 0o700)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        final = server_dir / f"{stamp}.tar.gz"
        partial = server_dir / f"{stamp}.tar.gz.partial"
        if final.exists():
            raise BackupError("A backup for this server was made in the same second; try again")
        relative = " ".join(shlex.quote(p.lstrip("/")) for p in self.paths)
        command = f"sudo tar --numeric-owner --one-file-system -czf - -C / {relative}"
        details = {"server_id": server_id, "hostname": ssh["hostname"], "paths": self.paths, "archive": final.name}
        await audit.record("files.backup.start", category="backup", resource_type="server", resource_id=server_id,
                           details=details)

        digest = hashlib.sha256()
        written = 0
        connector = SSHConnector(hostname=ssh["hostname"], username=ssh["username"], private_key=ssh.get("private_key"),
                                 password=ssh.get("password"), port=ssh.get("port") or 22, host_key=ssh.get("host_key"))
        returncode, stderr = 1, ""
        try:
            if not await connector.connect():
                raise BackupError(connector.connect_error or "SSH connection failed")
            with open(partial, "wb") as out:
                def write_chunk(chunk: bytes) -> None:
                    nonlocal written
                    out.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                returncode, stderr = await asyncio.to_thread(
                    connector.run_stream, command, download=write_chunk, timeout=FILE_TRANSFER_TIMEOUT)
            if returncode not in (0, 1):  # GNU tar exits 1 when a file changed while it was read; the archive is still usable
                raise BackupError(f"tar exited with status {returncode}: {stderr.strip()[-300:]}")
            if written == 0:
                raise BackupError("The archive from the server was empty")
            with gzip.open(partial, "rb") as gz:
                for _ in iter(lambda: gz.read(1 << 20), b""):
                    pass
        except BackupError as e:
            partial.unlink(missing_ok=True)
            await audit.record("files.backup.complete", category="backup", outcome="failure", resource_type="server",
                               resource_id=server_id, reason=str(e), details=details)
            raise
        except (OSError, EOFError, zlib.error) as e:
            partial.unlink(missing_ok=True)
            reason = f"The archive is not valid gzip: {e}"
            await audit.record("files.backup.complete", category="backup", outcome="failure", resource_type="server",
                               resource_id=server_id, reason=reason, details=details)
            raise BackupError(reason)
        except Exception as e:
            partial.unlink(missing_ok=True)
            reason = f"Backup transfer failed: {e}"
            await audit.record("files.backup.complete", category="backup", outcome="failure", resource_type="server",
                               resource_id=server_id, reason=reason, details=details)
            raise BackupError(reason)
        finally:
            await connector.disconnect()

        os.chmod(partial, 0o600)
        os.replace(partial, final)
        manifest = {
            "version": 1, "server_id": server_id, "hostname": ssh["hostname"],
            "created_at": datetime.now(timezone.utc).isoformat(), "paths": self.paths,
            "size_bytes": written, "sha256": digest.hexdigest(), "tar_status": returncode,
            "warnings": stderr.strip()[-1000:] if returncode == 1 else "",
        }
        tmp_manifest = server_dir / f"{stamp}.json.tmp"
        tmp_manifest.write_text(json.dumps(manifest, indent=2))
        os.chmod(tmp_manifest, 0o600)
        os.replace(tmp_manifest, server_dir / f"{stamp}.json")
        pruned = self._prune(server_dir)
        volid = f"{server_id}/{final.name}"
        await audit.record("files.backup.complete", category="backup", resource_type="server", resource_id=server_id,
                           details={**details, "size_bytes": written, "sha256": manifest["sha256"][:16],
                                    "tar_status": returncode, "pruned": pruned})
        return volid

    def _prune(self, server_dir: Path) -> int:
        """Keep the newest `keep` backups for a server. Runs only after a new backup has been verified and written."""
        removed = 0
        for manifest in sorted(server_dir.glob("*.json"), reverse=True)[self.keep:]:
            (server_dir / f"{manifest.stem}.tar.gz").unlink(missing_ok=True)
            manifest.unlink(missing_ok=True)
            removed += 1
        return removed

    async def list_backups(self, server_id: str) -> List[Dict[str, Any]]:
        server_dir = self._server_dir(server_id)
        if not server_dir.is_dir():
            return []
        backups = []
        for manifest_path in sorted(server_dir.glob("*.json"), reverse=True):
            try:
                manifest = json.loads(manifest_path.read_text())
            except (OSError, ValueError):
                continue
            archive = server_dir / f"{manifest_path.stem}.tar.gz"
            if archive.is_file():
                backups.append({"volid": f"{server_id}/{archive.name}", "ctime": _epoch(manifest.get("created_at")),
                                "size": manifest.get("size_bytes")})
        return backups

    def _locate(self, volid: str, server_id: str) -> Tuple[Path, Path]:
        match = _FILE_VOLID.match(volid or "")
        if not match:
            raise BackupError("That is not a file backup identifier")
        if match.group(1).lower() != str(server_id).lower():
            raise BackupError("That backup belongs to a different server")
        server_dir = self._server_dir(match.group(1))
        return server_dir / f"{match.group(2)}.tar.gz", server_dir / f"{match.group(2)}.json"

    async def restore_preflight(self, volid: str, server_id: str) -> Dict[str, Any]:
        """Checks the archive without touching the server: it exists, its manifest is readable, and size and SHA-256 match."""
        checks: List[Dict[str, Any]] = []
        errors: List[str] = []
        result: Dict[str, Any] = {
            "ok": False, "volid": volid, "kind": "files", "target_storage": None, "backup_size": None,
            "paths": [], "sha256": None, "disks": [], "storages": [], "candidates": [],
            "checks": checks, "errors": errors, "warnings": [FILE_RESTORE_WARNING],
        }

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": ok, "detail": detail})
            if not ok:
                errors.append(detail)

        try:
            archive, manifest_path = self._locate(volid, server_id)
        except BackupError as e:
            check("backup_identifier", False, str(e))
            return result
        check("backup_identifier", True, "The backup belongs to this server")
        if not archive.is_file():
            check("backup_present", False, f"Backup {archive.name} was not found on the share")
            return result
        check("backup_present", True, f"Backup {archive.name} is on the share")
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            check("manifest_readable", False, "The backup's manifest is missing or unreadable, so the archive cannot be verified")
            return result
        check("manifest_readable", True, "The backup's manifest can be read")
        result["paths"] = manifest.get("paths", [])
        result["sha256"] = manifest.get("sha256")
        size = archive.stat().st_size
        result["backup_size"] = size
        size_ok = size == manifest.get("size_bytes")
        check("size_matches", size_ok,
              f"Archive size {size} bytes matches its manifest" if size_ok
              else f"Archive is {size} bytes but its manifest says {manifest.get('size_bytes')}")
        if size_ok:
            digest_ok = _sha256_of(archive) == manifest.get("sha256")
            check("checksum_matches", digest_ok,
                  "Archive checksum matches its manifest" if digest_ok
                  else "Archive checksum does not match its manifest; the copy on the share may be damaged")
        result["ok"] = not errors
        return result

    async def restore(self, server: Dict[str, Any], volid: str) -> None:
        from app.services import audit
        server_id, ssh = server["id"], server["ssh"]
        details = {"server_id": server_id, "hostname": ssh["hostname"], "volid": volid}
        await audit.record("files.restore.start", category="backup", resource_type="server", resource_id=server_id,
                           details=details)
        preflight = await self.restore_preflight(volid, server_id)
        await audit.record("files.restore.preflight", category="backup", resource_type="server", resource_id=server_id,
                           outcome="success" if preflight["ok"] else "failure",
                           reason="; ".join(preflight["errors"]) or None, details=details)
        if not preflight["ok"]:
            raise BackupError("Restore blocked, nothing was changed: " + "; ".join(preflight["errors"]))
        archive, _ = self._locate(volid, server_id)
        size = preflight["backup_size"]
        remote = f"/var/tmp/jarn-restore-{uuid.uuid4().hex}.tar.gz"
        connector = SSHConnector(hostname=ssh["hostname"], username=ssh["username"], private_key=ssh.get("private_key"),
                                 password=ssh.get("password"), port=ssh.get("port") or 22, host_key=ssh.get("host_key"))
        try:
            if not await connector.connect():
                raise BackupError(connector.connect_error or "SSH connection failed")
            space = await connector.execute_command("df -P -B1 /var/tmp | tail -1 | awk '{print $4}'", timeout=30)
            free = int(space.stdout.strip()) if space.returncode == 0 and space.stdout.strip().isdigit() else None
            if free is None or free < size * RESTORE_HEADROOM:
                raise BackupError(f"Not enough free space in /var/tmp on the server; about {int(size * RESTORE_HEADROOM)} bytes are needed. Nothing was changed")
            # The archive is staged on the server and checked there before anything is extracted
            code, stderr = await asyncio.to_thread(
                connector.run_stream, f"umask 077 && cat > {remote}", upload_path=str(archive), timeout=FILE_TRANSFER_TIMEOUT)
            if code != 0:
                raise BackupError(f"Could not send the archive to the server: {stderr.strip()[-300:]}")
            verify = await connector.execute_command(
                f"echo {shlex.quote(preflight['sha256'] + '  ' + remote)} | sha256sum -c -", timeout=FILE_TRANSFER_TIMEOUT)
            if verify.returncode != 0:
                raise BackupError("The archive on the server does not match its checksum, so nothing was extracted")
            code, stderr = await asyncio.to_thread(
                connector.run_stream, f"sudo tar --numeric-owner -xzpf {remote} -C /", timeout=FILE_TRANSFER_TIMEOUT)
            if code != 0:
                raise BackupError(f"Extracting the archive failed with status {code}. Some files may already be restored, "
                                  f"so check the server before continuing. Cause: {stderr.strip()[-300:]}")
        except BackupError as e:
            await audit.record("files.restore.complete", category="backup", outcome="failure", resource_type="server",
                               resource_id=server_id, reason=str(e), details=details)
            raise
        except Exception as e:
            reason = f"Restore failed: {e}"
            await audit.record("files.restore.complete", category="backup", outcome="failure", resource_type="server",
                               resource_id=server_id, reason=reason, details=details)
            raise BackupError(reason)
        finally:
            try:
                if connector.connected:
                    await connector.execute_command(f"rm -f {remote}", timeout=30)
            finally:
                await connector.disconnect()
        await audit.record("files.restore.complete", category="backup", resource_type="server", resource_id=server_id,
                           details=details)


def server_ref(server_id: Any, hostname: str, ip: Any, port: Any, credentials: Optional[Dict[str, Any]],
               metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """What a backup needs to know about a server: its id, its mapping metadata and how to reach it over SSH."""
    creds = dict(credentials or {})
    meta = dict(metadata or {})
    return {
        "id": str(server_id),
        "metadata": meta,
        "ssh": {
            "hostname": str(ip) if ip else hostname,
            "username": creds.get("username"),
            "private_key": creds.get("private_key"),
            "password": creds.get("password"),
            "port": int(port) if port else 22,
            "host_key": pinned_host_key(meta),
        },
    }


def targets_from_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    if config and config.get("targets") is not None:
        return list(config["targets"])
    if config and config.get("api_url"):
        return [{
            "id": "default",
            "name": "Default",
            "provider": config.get("provider", "proxmox"),
            "api_url": config.get("api_url"),
            "token_id": config.get("token_id"),
            "token_secret": config.get("token_secret"),
            "storage": config.get("storage"),
            "verify_tls": bool(config.get("verify_tls", False)),
        }]
    return []


def provider_from_config(config: Dict[str, Any], target_id: Optional[str] = None):
    """The provider for a configured target: ProxmoxProvider or FileProvider, depending on the target's provider."""
    if not config or not config.get("enabled"):
        raise BackupError("Backup is not enabled")
    targets = targets_from_config(config)
    if target_id is None:
        if len(targets) != 1:
            raise BackupError("Server is not assigned to a backup target")
        target = targets[0]
    else:
        target = next((t for t in targets if t.get("id") == target_id), None)
        if target is None:
            raise BackupError(f"Backup target '{target_id}' is not configured")
    if target.get("provider") == "nfs":
        if not target.get("base_path"):
            raise BackupError(f"Backup target '{target.get('name', target.get('id'))}' needs a folder to store backups in")
        return FileProvider(
            base_path=target["base_path"],
            paths=target.get("paths") or None,
            keep=int(target.get("keep") or FILE_DEFAULT_KEEP),
            require_mount=bool(target.get("require_mount", True)),
        )
    if target.get("provider") != "proxmox":
        raise BackupError(f"Unsupported backup provider: {target.get('provider')}")
    # storage is an optional fallback; the storage used for each VM comes from its Datacenter backup job
    missing = [k for k in ("api_url", "token_id", "token_secret") if not target.get(k)]
    if missing:
        raise BackupError(f"Backup target '{target.get('name', target.get('id'))}' is missing: {', '.join(missing)}")
    return ProxmoxProvider(
        api_url=target["api_url"],
        token_id=target["token_id"],
        token_secret=target["token_secret"],
        storage=target.get("storage") or None,
        verify_tls=bool(target.get("verify_tls", False)),
    )


def provider_for_mapping(config: Dict[str, Any], mapping: Dict[str, Any]):
    """The provider a server's mapping points at. A mapping whose kind no longer matches its target must be redone."""
    provider = provider_from_config(config, mapping.get("target"))
    is_file = mapping.get("type") == "file"
    if is_file != isinstance(provider, FileProvider):
        raise BackupError("This server's backup target changed kind; map the server to its backup source again in Admin Settings")
    return provider


def mapping_from_server(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    mapping = (metadata or {}).get("backup")
    if not mapping:
        return None
    if mapping.get("type") == "file":
        return mapping if mapping.get("target") else None
    # Proxmox mappings from before file targets existed may have no target; the single configured target is used then
    if not mapping.get("node") or not mapping.get("vmid") or not mapping.get("type"):
        return None
    return mapping


async def take_server_backup(config: Dict[str, Any], server: Dict[str, Any]) -> str:
    """Backs up a server through its mapped backup source. Returns a reference that restore_server_from_backup takes."""
    mapping = mapping_from_server(server["metadata"])
    if not mapping:
        raise BackupError("Server is not mapped to a backup source in Admin Settings")
    provider = provider_for_mapping(config, mapping)
    if isinstance(provider, FileProvider):
        return await provider.backup(server)
    return await provider.backup(mapping["node"], int(mapping["vmid"]))


async def restore_server_from_backup(config: Dict[str, Any], server: Dict[str, Any], volid: str,
                                     target_storage: Optional[str] = None) -> None:
    mapping = mapping_from_server(server["metadata"])
    if not mapping:
        raise BackupError("Server is not mapped to a backup source in Admin Settings")
    provider = provider_for_mapping(config, mapping)
    if isinstance(provider, FileProvider):
        if target_storage:
            raise BackupError("File backups are restored in place; a target storage does not apply")
        await provider.restore(server, volid)
        return
    await provider.restore(mapping["node"], mapping["type"], int(mapping["vmid"]), volid, target_storage)
