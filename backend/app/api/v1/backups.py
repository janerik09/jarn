from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.logging_config import get_logger
from app.models import Server
from app.models.settings import SystemSettings
from app.services.backup_providers import (
    BackupError, FileProvider, mapping_from_server, provider_for_mapping, provider_from_config, targets_from_config,
)

logger = get_logger(__name__)
router = APIRouter()


class MappingRequest(BaseModel):
    target: Optional[str] = None  # Backup target id; optional for a single configured target
    type: str  # "qemu" or "lxc" for a Proxmox guest, "file" for a file target
    node: Optional[str] = None  # Proxmox only
    vmid: Optional[int] = None  # Proxmox only


class RestoreRequest(BaseModel):
    volid: str
    confirm: bool = False
    target_storage: Optional[str] = None  # None restores each disk to its original storage; Proxmox only


async def _config(db: AsyncSession) -> dict:
    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    return dict(row.backup_config or {}) if row else {}


async def _server(db: AsyncSession, server_id: str) -> Server:
    server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


def _provider(config: dict, target_id=None):
    try:
        return provider_from_config(config, target_id)
    except BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


async def _mapped_provider(db: AsyncSession, server: Server):
    mapping = mapping_from_server(server.metadata_)
    if not mapping:
        raise HTTPException(status_code=400, detail="Server is not mapped to a backup source in Admin Settings")
    try:
        return mapping, provider_for_mapping(await _config(db), mapping)
    except BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/backup/test")
async def test_backup_connection(db: AsyncSession = Depends(get_db)):
    config = await _config(db)
    results = []
    for target in targets_from_config(config):
        entry = {"target": target.get("id"), "name": target.get("name"), "provider": target.get("provider", "proxmox")}
        try:
            entry.update({"ok": True, **(await provider_from_config(config, target.get("id")).test())})
        except BackupError as e:
            entry.update({"ok": False, "message": str(e)})
        results.append(entry)
    if not results:
        raise HTTPException(status_code=400, detail="No backup targets are configured")
    return {"results": results}


@router.post("/backup/guests")
async def list_guests(db: AsyncSession = Depends(get_db)):
    config = await _config(db)
    guests, errors = [], []
    for target in targets_from_config(config):
        if target.get("provider", "proxmox") != "proxmox":
            continue  # file targets have no guests to list
        try:
            for guest in await provider_from_config(config, target.get("id")).list_guests():
                guests.append({**guest, "target": target.get("id"), "target_name": target.get("name")})
        except BackupError as e:
            errors.append({"target": target.get("id"), "name": target.get("name"), "message": str(e)})
    if not guests and errors:
        raise HTTPException(status_code=400, detail="; ".join(f"{e['name']}: {e['message']}" for e in errors))
    return {"guests": guests, "errors": errors}


@router.get("/backup/mappings")
async def list_mappings(db: AsyncSession = Depends(get_db)):
    servers = (await db.execute(select(Server).order_by(Server.hostname))).scalars().all()
    file_targets = [
        {"id": t.get("id"), "name": t.get("name") or t.get("id")}
        for t in targets_from_config(await _config(db)) if t.get("provider") == "nfs"
    ]
    return {
        "file_targets": file_targets,
        "mappings": [
            {
                "server_id": str(s.id),
                "hostname": s.hostname,
                "mapping": mapping_from_server(s.metadata_),
                "last_backup": (s.metadata_ or {}).get("last_backup"),
            }
            for s in servers
        ],
    }


@router.put("/backup/mappings/{server_id}")
async def set_mapping(server_id: str, request: Optional[MappingRequest] = None, db: AsyncSession = Depends(get_db)):
    server = await _server(db, server_id)
    from app.services import audit as _audit
    _before = _audit.copy_json({"backup": (server.metadata_ or {}).get("backup")})
    metadata = dict(server.metadata_ or {})
    if request is None:
        metadata.pop("backup", None)
    else:
        config = await _config(db)
        target_id = request.target
        if target_id is None:
            targets = targets_from_config(config)
            if len(targets) != 1:
                raise HTTPException(status_code=400, detail="Choose a backup target for this server")
            target_id = targets[0].get("id")
        provider = _provider(config, target_id)
        if request.type == "file":
            if not isinstance(provider, FileProvider):
                raise HTTPException(status_code=400, detail="Only a file target can back up a server's files")
            mapping = {"target": target_id, "type": "file"}
        elif request.type in ("qemu", "lxc"):
            if isinstance(provider, FileProvider):
                raise HTTPException(status_code=400, detail="Choose a Proxmox target for a VM or container")
            if request.node is None or request.vmid is None:
                raise HTTPException(status_code=400, detail="node and vmid are required for a Proxmox VM or container")
            try:
                guest_os = await provider.guest_os(request.node, request.type, request.vmid)
            except BackupError as e:
                raise HTTPException(status_code=400, detail=str(e))
            if guest_os == "windows":
                raise HTTPException(status_code=400, detail=f"VM {request.vmid} is a Windows VM and cannot be mapped to a Linux server")
            mapping = {"target": target_id, "node": request.node, "vmid": request.vmid, "type": request.type}
        else:
            raise HTTPException(status_code=400, detail="type must be qemu, lxc or file")
        metadata["backup"] = mapping
    server.metadata_ = metadata
    await db.commit()
    await _audit.record('config.update', category='backup', resource_type='server', resource_id=str(server.id),
                        details={'changes': _audit.diff(_before, _audit.copy_json({'backup': metadata.get('backup')}))})
    return {"server_id": str(server.id), "mapping": mapping_from_server(metadata)}


@router.get("/servers/{server_id}/backups")
async def list_server_backups(server_id: str, db: AsyncSession = Depends(get_db)):
    server = await _server(db, server_id)
    mapping, provider = await _mapped_provider(db, server)
    try:
        if isinstance(provider, FileProvider):
            return {"backups": await provider.list_backups(str(server.id))}
        return {"backups": await provider.list_backups(mapping["node"], int(mapping["vmid"]))}
    except BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/servers/{server_id}/backup", status_code=202)
async def start_server_backup(server_id: str, db: AsyncSession = Depends(get_db)):
    from app.tasks import run_manual_backup

    server = await _server(db, server_id)
    await _mapped_provider(db, server)
    task = run_manual_backup.delay(str(server.id))
    logger.info("manual_backup_queued", server_id=str(server.id), task_id=task.id)
    return {"task_id": task.id, "status": "queued"}


async def _restore_preflight(db: AsyncSession, server: Server, volid: str, target_storage: Optional[str]) -> dict:
    mapping, provider = await _mapped_provider(db, server)
    try:
        if isinstance(provider, FileProvider):
            if target_storage:
                raise BackupError("File backups are restored in place; a target storage does not apply")
            return await provider.restore_preflight(volid, str(server.id))
        return await provider.restore_preflight(mapping["node"], mapping["type"], int(mapping["vmid"]), volid, target_storage)
    except BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/servers/{server_id}/restore-preflight")
async def restore_preflight(server_id: str, volid: str, target_storage: Optional[str] = None,
                            db: AsyncSession = Depends(get_db)):
    """Checks a restore would run, without changing anything. Shown to the user before they confirm a restore."""
    server = await _server(db, server_id)
    return await _restore_preflight(db, server, volid, target_storage or None)


@router.post("/servers/{server_id}/restore", status_code=202)
async def start_server_restore(server_id: str, request: RestoreRequest, db: AsyncSession = Depends(get_db)):
    from app.tasks import run_manual_restore

    if not request.confirm:
        raise HTTPException(status_code=400, detail="Restore discards changes made since the backup. Confirm to continue.")
    server = await _server(db, server_id)
    target = request.target_storage or None
    preflight = await _restore_preflight(db, server, request.volid, target)
    if not preflight["ok"]:
        raise HTTPException(status_code=400, detail="Restore blocked, nothing was changed: " + "; ".join(preflight["errors"]))
    task = run_manual_restore.delay(str(server.id), request.volid, target)
    logger.info("manual_restore_queued", server_id=str(server.id), volid=request.volid, target_storage=target, task_id=task.id)
    return {"task_id": task.id, "status": "queued"}


@router.get("/backup/tasks/{task_id}")
async def backup_task_status(task_id: str):
    from celery.result import AsyncResult
    from app.celery_app import app as celery_app

    result = AsyncResult(task_id, app=celery_app)
    if result.state == "SUCCESS":
        return {"task_id": task_id, "state": result.state, "result": result.result}
    if result.state == "FAILURE":
        return {"task_id": task_id, "state": result.state, "error": str(result.result)}
    return {"task_id": task_id, "state": result.state}
