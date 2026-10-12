from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models.settings import SystemSettings
from app.services.backup_providers import targets_from_config
from app.logging_config import get_logger
from typing import List, Literal, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import field_validator

logger = get_logger(__name__)
router = APIRouter(tags=["admin"])


class PatchingConfig(BaseModel):
    """Configuration for Linux patch management"""
    max_concurrent_servers: int = 5  # Max servers to patch simultaneously
    pre_patch_health_check: bool = True  # Verify services before patching
    post_patch_validation: bool = True  # Verify services after patching
    automatic_rollback_on_failure: bool = True  # Auto-rollback if validation fails
    max_downtime_minutes: int = 120  # Maximum allowed downtime per server


class GeneralConfig(BaseModel):
    """Display preferences applied across the UI. Add new general settings here."""
    default_page_size: Literal[10, 25, 50, 100] = 25  # Rows per page on every paginated list
    scan_interval_hours: Literal[0, 1, 6, 12, 24] = 6  # Rescan servers whose last scan is older; 0 disables
    timezone: str = "UTC"  # IANA zone used to display times in the UI
    kubernetes_control_server_id: Optional[str] = None  # Server with admin kubeconfig that runs drains and Ready checks

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"Unknown timezone: {value}")
        return value


class BackupTargetUpdate(BaseModel):
    """One Proxmox VE endpoint. Leave token_secret empty to keep the stored secret for this target."""
    id: str
    name: str
    provider: Literal["proxmox"] = "proxmox"
    api_url: Optional[str] = None  # e.g. https://pve1.example.com:8006
    token_id: Optional[str] = None  # e.g. backup@pve!patchmanager
    token_secret: Optional[str] = None
    storage: Optional[str] = None  # Optional fallback storage; per-VM storage comes from Datacenter backup jobs
    verify_tls: bool = False


class BackupConfigUpdate(BaseModel):
    enabled: bool = False
    targets: List[BackupTargetUpdate] = []

    @field_validator("targets")
    @classmethod
    def _unique_targets(cls, value):
        ids = [t.id for t in value]
        names = [t.name.strip().lower() for t in value]
        if len(set(ids)) != len(ids):
            raise ValueError("Each backup target needs a unique id")
        if len(set(names)) != len(names):
            raise ValueError("Backup target names must be unique")
        return value


class SystemSettingsUpdate(BaseModel):
    general: Optional[GeneralConfig] = None
    backup: Optional[BackupConfigUpdate] = None
    patching_config: Optional[PatchingConfig] = None
    auto_approve_critical: bool = False  # Auto-approve critical security patches
    require_approval_for_kernel: bool = True  # Require approval for kernel updates
    maintenance_window_start: Optional[str] = None  # Format: "HH:MM UTC"
    maintenance_window_end: Optional[str] = None    # Format: "HH:MM UTC"


class SystemSettingsResponse(BaseModel):
    general: GeneralConfig
    backup: dict = {}
    patching_config: Optional[dict] = None
    auto_approve_critical: bool
    require_approval_for_kernel: bool
    maintenance_window_start: Optional[str] = None
    maintenance_window_end: Optional[str] = None

    class Config:
        from_attributes = True


def _public_backup(config: Optional[dict]) -> dict:
    config = config or {}
    return {
        "enabled": bool(config.get("enabled", False)),
        "targets": [
            {
                "id": t.get("id"),
                "name": t.get("name") or t.get("id"),
                "provider": t.get("provider", "proxmox"),
                "api_url": t.get("api_url"),
                "token_id": t.get("token_id"),
                "storage": t.get("storage"),
                "verify_tls": bool(t.get("verify_tls", False)),
                "token_secret_set": bool(t.get("token_secret")),
            }
            for t in targets_from_config(config)
        ],
    }


def _to_response(settings: SystemSettings) -> dict:
    return {
        "general": GeneralConfig(**(settings.general_config or {})).model_dump(),
        "backup": _public_backup(settings.backup_config),
        "patching_config": settings.patching_config,
        "auto_approve_critical": settings.auto_approve_critical == "t",
        "require_approval_for_kernel": settings.require_approval_for_kernel == "t",
        "maintenance_window_start": settings.maintenance_window_start,
        "maintenance_window_end": settings.maintenance_window_end,
    }


@router.get("/settings", response_model=SystemSettingsResponse)
async def get_settings(db: AsyncSession = Depends(get_db)):
    """Get system settings"""
    try:
        result = await db.execute(select(SystemSettings).limit(1))
        settings = result.scalar_one_or_none()
        
        if not settings:
            # Create default settings
            settings = SystemSettings()
            db.add(settings)
            await db.commit()
            await db.refresh(settings)
        
        return _to_response(settings)
    except Exception as e:
        logger.error("get_settings_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/settings", response_model=SystemSettingsResponse)
async def update_settings(
    request: SystemSettingsUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update system settings"""
    try:
        result = await db.execute(select(SystemSettings).limit(1))
        settings = result.scalar_one_or_none()
        
        if not settings:
            settings = SystemSettings()
            db.add(settings)
        
        from app.services import audit as _audit
        _fields = ["auto_approve_critical", "require_approval_for_kernel", "maintenance_window_start",
                   "maintenance_window_end", "patching_config", "general_config", "backup_config"]
        _before = _audit.snapshot_fields(settings, _fields)

        # Update fields
        settings.auto_approve_critical = "t" if request.auto_approve_critical else "f"
        settings.require_approval_for_kernel = "t" if request.require_approval_for_kernel else "f"
        settings.maintenance_window_start = request.maintenance_window_start
        settings.maintenance_window_end = request.maintenance_window_end

        if request.patching_config:
            settings.patching_config = request.patching_config.model_dump()

        if request.general:
            settings.general_config = request.general.model_dump()

        if request.backup:
            existing = {t["id"]: t for t in targets_from_config(dict(settings.backup_config or {}))}
            targets = []
            for target in request.backup.targets:
                data = target.model_dump()
                if not data["token_secret"]:
                    data["token_secret"] = existing.get(target.id, {}).get("token_secret")
                targets.append(data)
            settings.backup_config = {"enabled": request.backup.enabled, "targets": targets}

        await db.commit()
        await db.refresh(settings)
        _changes = _audit.diff(_before, _audit.snapshot_fields(settings, _fields))
        await _audit.record("config.update", category="config", resource_type="settings",
                            details={"changes": _changes})

        logger.info("settings_updated", critical_auto_approve=request.auto_approve_critical)
        
        return _to_response(settings)
    except Exception as e:
        logger.error("update_settings_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


class SmtpUpdate(BaseModel):
    # Leave host empty to turn email off. Leave password empty to keep the stored password.
    host: Optional[str] = None
    port: int = 587
    username: Optional[str] = None
    password: Optional[str] = None
    from_address: Optional[str] = None
    to: Optional[str] = None
    starttls: bool = True

    @field_validator("port")
    @classmethod
    def _port(cls, v: int) -> int:
        if not 1 <= v <= 65535:
            raise ValueError("port must be between 1 and 65535")
        return v


class NotificationUpdate(BaseModel):
    # Empty or null clears the webhook. The URL is stored encrypted and is never returned to the browser.
    webhook_url: Optional[str] = None
    smtp: Optional[SmtpUpdate] = None

    @field_validator("webhook_url")
    @classmethod
    def _valid_url(cls, v: Optional[str]) -> Optional[str]:
        if not v or not v.strip():
            return None
        v = v.strip()
        if not (v.startswith("https://") or v.startswith("http://")) or " " in v:
            raise ValueError("The webhook must be an http:// or https:// URL")
        return v


def _smtp_view(smtp: dict) -> dict:
    return {
        "host": smtp.get("host") or "",
        "port": smtp.get("port") or 587,
        "username": smtp.get("username") or "",
        "from_address": smtp.get("from") or "",
        "to": smtp.get("to") or "",
        "starttls": bool(smtp.get("starttls", True)),
        "password_set": bool(smtp.get("password")),
    }


@router.get("/notifications")
async def get_notifications(db: AsyncSession = Depends(get_db)):
    row = await _settings_or_create(db)
    config = row.notification_config or {}
    return {
        "webhook_configured": bool(config.get("webhook_url")),
        "smtp": _smtp_view(config.get("smtp") or {}),
    }


@router.put("/notifications")
async def set_notifications(payload: NotificationUpdate, db: AsyncSession = Depends(get_db)):
    from app.services import audit as _audit
    row = await _settings_or_create(db)
    config = dict(row.notification_config or {})
    _before = _audit.redact(dict(config))
    config["webhook_url"] = payload.webhook_url
    if payload.smtp is not None:
        previous = dict(config.get("smtp") or {})
        smtp = payload.smtp
        if not smtp.host:
            config["smtp"] = {}
        else:
            if smtp.from_address and "@" not in smtp.from_address:
                raise HTTPException(status_code=400, detail="From address must be an email address")
            config["smtp"] = {
                "host": smtp.host.strip(),
                "port": smtp.port,
                "username": smtp.username or None,
                "password": smtp.password if smtp.password else previous.get("password"),
                "from": (smtp.from_address or "").strip(),
                "to": (smtp.to or "").strip(),
                "starttls": smtp.starttls,
            }
    row.notification_config = config
    await db.commit()
    await _audit.record("config.update", category="config", resource_type="notifications",
                        details={"changes": _audit.diff(_before, _audit.redact(dict(config)))})
    return {
        "webhook_configured": bool(payload.webhook_url),
        "smtp": _smtp_view(config.get("smtp") or {}),
    }


@router.post("/notifications/test")
async def test_notifications():
    from app.services import notify
    cfg = await notify.config()
    if not cfg.get("webhook_url") and not cfg.get("smtp", {}).get("host"):
        raise HTTPException(status_code=400, detail="Save a webhook URL or email settings first")
    result = {"webhook": None, "email": None}
    if cfg.get("webhook_url"):
        result["webhook"] = await notify.send("test", "JÁRN test notification",
                                              "If you can read this, notifications are working.")
    if notify._email_settings(cfg):
        result["email"] = await notify.send_email(notify._email_settings(cfg), "JÁRN test notification",
                                                  "If you can read this, email notifications are working.")
    if result["webhook"] is False or result["email"] is False:
        raise HTTPException(status_code=502, detail=f"Delivery failed: {result}. Check the settings and the server log.")
    return {"sent": True, **result}


async def _settings_or_create(db: AsyncSession):
    from app.models.settings import SystemSettings
    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = SystemSettings()
        db.add(row)
        await db.flush()
    return row
