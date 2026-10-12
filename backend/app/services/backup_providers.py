"""
Backup providers for pre-patch backups and restores.

Proxmox VE (with a Proxmox Backup Server storage) is the first provider. Each managed server maps to a
VM or container through metadata_["backup"] = {"node", "vmid", "type"}.
"""

import asyncio
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

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


def provider_from_config(config: Dict[str, Any], target_id: Optional[str] = None) -> ProxmoxProvider:
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


def mapping_from_server(metadata: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    mapping = (metadata or {}).get("backup")
    if not mapping or not mapping.get("node") or not mapping.get("vmid") or not mapping.get("type"):
        return None
    return mapping
