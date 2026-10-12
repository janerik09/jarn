from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.logging_config import get_logger
from app.models import Server
from app.models.settings import SystemSettings
from app.services.backup_providers import BackupError, mapping_from_server, provider_from_config, targets_from_config

logger = get_logger(__name__)
router = APIRouter()


class MappingRequest(BaseModel):
    target: Optional[str] = None  # Backup target id; optional for a single configured target
    node: str
    vmid: int
    type: str  # "qemu" or "lxc"


class RestoreRequest(BaseModel):
    volid: str
    confirm: bool = False
    target_storage: Optional[str] = None  # None restores each disk to its original storage


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


@router.post("/backup/test")
async def test_backup_connection(db: AsyncSession = Depends(get_db)):
    config = await _config(db)
    results = []
    for target in targets_from_config(config):
        entry = {"target": target.get("id"), "name": target.get("name")}
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
    return {
        "mappings": [
            {
                "server_id": str(s.id),
                "hostname": s.hostname,
                "mapping": mapping_from_server(s.metadata_),
                "last_backup": (s.metadata_ or {}).get("last_backup"),
            }
            for s in servers
        ]
    }


@router.put("/backup/mappings/{server_id}")
async def set_mapping(server_id: str, request: Optional[MappingRequest] = None, db: AsyncSession = Depends(get_db)):
    server = await _server(db, server_id)
    from app.services import audit as _audit
    _before = _audit.copy_json({"backup": (server.metadata_ or {}).get("backup")})
    if request is not None and request.type not in ("qemu", "lxc"):
        raise HTTPException(status_code=400, detail="type must be qemu or lxc")
    if request is not None and request.target is not None:
        provider = _provider(await _config(db), request.target)
        try:
            guest_os = await provider.guest_os(request.node, request.type, request.vmid)
        except BackupError as e:
            raise HTTPException(status_code=400, detail=str(e))
        if guest_os == "windows":
            raise HTTPException(status_code=400, detail=f"VM {request.vmid} is a Windows VM and cannot be mapped to a Linux server")
    metadata = dict(server.metadata_ or {})
    if request is None:
        metadata.pop("backup", None)
    else:
        if request.target is None:
            targets = targets_from_config(await _config(db))
            if len(targets) != 1:
                raise HTTPException(status_code=400, detail="Choose a backup target for this server")
            request.target = targets[0].get("id")
        metadata["backup"] = request.model_dump()
    server.metadata_ = metadata
    await db.commit()
    await _audit.record('config.update', category='backup', resource_type='server', resource_id=str(server.id),
                        details={'changes': _audit.diff(_before, _audit.copy_json({'backup': metadata.get('backup')}))})
    return {"server_id": str(server.id), "mapping": mapping_from_server(metadata)}


@router.get("/servers/{server_id}/backups")
async def list_server_backups(server_id: str, db: AsyncSession = Depends(get_db)):
    server = await _server(db, server_id)
    mapping = mapping_from_server(server.metadata_)
    if not mapping:
        raise HTTPException(status_code=400, detail="Server is not mapped to a Proxmox VM or container")
    provider = _provider(await _config(db), mapping.get("target"))
    try:
        return {"backups": await provider.list_backups(mapping["node"], int(mapping["vmid"]))}
    except BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/servers/{server_id}/backup", status_code=202)
async def start_server_backup(server_id: str, db: AsyncSession = Depends(get_db)):
    from app.tasks import run_manual_backup

    server = await _server(db, server_id)
    if not mapping_from_server(server.metadata_):
        raise HTTPException(status_code=400, detail="Server is not mapped to a Proxmox VM or container")
    _provider(await _config(db), mapping_from_server(server.metadata_).get("target"))
    task = run_manual_backup.delay(str(server.id))
    logger.info("manual_backup_queued", server_id=str(server.id), task_id=task.id)
    return {"task_id": task.id, "status": "queued"}


async def _restore_preflight(db: AsyncSession, server: Server, volid: str, target_storage: Optional[str]) -> dict:
    mapping = mapping_from_server(server.metadata_)
    if not mapping:
        raise HTTPException(status_code=400, detail="Server is not mapped to a Proxmox VM or container")
    provider = _provider(await _config(db), mapping.get("target"))
    try:
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
