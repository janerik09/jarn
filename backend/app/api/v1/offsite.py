"""
Off-host copy settings: where database dumps and audit archives are sent over SFTP. Admin only.
Secrets (private key, password) are stored encrypted and are never returned; the API only says whether they are set.
"""

import asyncio
from typing import Any, Dict, Literal, Optional

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.database import get_db
from app.logging_config import get_logger
from app.models import User
from app.models.settings import SystemSettings
from app.services import audit, offsite

logger = get_logger(__name__)
router = APIRouter()
SECRETS = ("private_key", "password")


class OffsiteUpdate(BaseModel):
    enabled: bool = False
    host: Optional[str] = None
    port: int = Field(22, ge=1, le=65535)
    username: Optional[str] = None
    auth: Literal["key", "password"] = "key"
    private_key: Optional[str] = None  # blank keeps the stored key
    password: Optional[str] = None  # blank keeps the stored password
    remote_dir: Optional[str] = None  # absolute path on the remote server
    keep_copies: int = Field(14, ge=offsite.MIN_KEEP, le=offsite.MAX_KEEP)
    host_key: Optional[str] = None  # the server's host key, approved after a successful test

    @field_validator("host", "username", "remote_dir")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value


def _public(config: Dict[str, Any]) -> Dict[str, Any]:
    from app.services.offsite_status import evaluate
    host_key = config.get("host_key")
    return {
        "state": evaluate(config)["state"],
        "waiver": config.get("waiver"),
        "enabled": bool(config.get("enabled", False)),
        "host": config.get("host"),
        "port": int(config.get("port") or 22),
        "username": config.get("username"),
        "auth": config.get("auth", "key"),
        "remote_dir": config.get("remote_dir"),
        "keep_copies": int(config.get("keep_copies") or 14),
        "host_key": host_key,
        "host_key_fingerprint": offsite.fingerprint_of(host_key) if host_key else None,
        "private_key_set": bool(config.get("private_key")),
        "password_set": bool(config.get("password")),
        "last_run": config.get("last_run"),
    }


def _validate(cfg: Dict[str, Any]) -> None:
    if not cfg.get("enabled"):
        return
    missing = [name for name, label in (("host", "server"), ("username", "username"), ("remote_dir", "remote folder"))
               if not cfg.get(name)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Enter the {', '.join(missing)} for the off-host copy")
    if not str(cfg["remote_dir"]).startswith("/") or str(cfg["remote_dir"]).rstrip("/") == "":
        raise HTTPException(status_code=400, detail="The remote folder must be an absolute path such as /volume1/jarn-backups")
    if not cfg.get("host_key"):
        raise HTTPException(status_code=400, detail="Test the connection and approve the server's host key before turning the copy on")
    if cfg.get("auth") == "key" and not cfg.get("private_key"):
        raise HTTPException(status_code=400, detail="Enter the SSH private key for the off-host copy")
    if cfg.get("auth") == "password" and not cfg.get("password"):
        raise HTTPException(status_code=400, detail="Enter the password for the off-host copy")


def _merge(stored: Dict[str, Any], update: OffsiteUpdate) -> Dict[str, Any]:
    cfg = update.model_dump()
    for name in SECRETS:
        if not cfg.get(name):
            cfg[name] = stored.get(name)
    cfg["last_run"] = stored.get("last_run")
    cfg["waiver"] = stored.get("waiver")
    return cfg


async def _row(db: AsyncSession) -> SystemSettings:
    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = SystemSettings()
        db.add(row)
    return row


@router.get("")
async def get_offsite(db: AsyncSession = Depends(get_db)):
    row = await _row(db)
    return _public(dict(row.offsite_config or {}))


@router.put("")
async def set_offsite(update: OffsiteUpdate, db: AsyncSession = Depends(get_db)):
    row = await _row(db)
    stored = dict(row.offsite_config or {})
    before = _public(stored)
    cfg = _merge(stored, update)
    _validate(cfg)
    row.offsite_config = cfg
    await db.commit()
    after = _public(cfg)
    await audit.record("config.update", category="config", resource_type="offsite",
                       details={"changes": audit.diff(before, after)})
    logger.info("offsite_settings_updated", enabled=cfg["enabled"])
    return after


@router.post("/test")
async def test_offsite(update: OffsiteUpdate, db: AsyncSession = Depends(get_db)):
    """Connects and checks the remote folder. Writes only a short test file, then removes it. Shows the host key to approve."""
    row = await _row(db)
    cfg = _merge(dict(row.offsite_config or {}), update)
    if not cfg.get("host") or not cfg.get("username") or not cfg.get("remote_dir"):
        raise HTTPException(status_code=400, detail="Enter the server, username and remote folder first")
    try:
        result = await asyncio.to_thread(offsite.test, cfg)
    except offsite.OffsiteError as e:
        await audit.record("offsite.test", category="backup", outcome="failure", reason=str(e),
                           details={"host": cfg.get("host")})
        raise HTTPException(status_code=400, detail=str(e))
    await audit.record("offsite.test", category="backup", details={"host": cfg.get("host")})
    return result


@router.post("/run", status_code=202)
async def run_offsite(db: AsyncSession = Depends(get_db)):
    from app.tasks import run_offsite_backup

    row = await _row(db)
    stored = dict(row.offsite_config or {})
    if not stored.get("enabled"):
        raise HTTPException(status_code=400, detail="Turn the off-host copy on and save it first")
    _validate(stored)
    task = run_offsite_backup.delay(True)
    logger.info("offsite_run_queued", task_id=task.id)
    return {"task_id": task.id, "status": "queued"}


class WaiverRequest(BaseModel):
    reason: str = Field(min_length=5, max_length=500)


@router.post("/waiver")
async def record_waiver(request: WaiverRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Accept the risk of running without an off-host copy. Shown on the dashboard until the copy is turned on."""
    row = await _row(db)
    stored = dict(row.offsite_config or {})
    stored["waiver"] = {"reason": request.reason.strip(), "by": user.username, "at": datetime.utcnow().isoformat()}
    row.offsite_config = stored
    await db.commit()
    await audit.record("offsite.waiver", category="backup", outcome="info",
                       reason=request.reason.strip(), details={"action": "recorded"})
    return _public(stored)


@router.delete("/waiver")
async def remove_waiver(db: AsyncSession = Depends(get_db)):
    row = await _row(db)
    stored = dict(row.offsite_config or {})
    stored.pop("waiver", None)
    row.offsite_config = stored
    await db.commit()
    await audit.record("offsite.waiver", category="backup", outcome="info", details={"action": "removed"})
    return _public(stored)
