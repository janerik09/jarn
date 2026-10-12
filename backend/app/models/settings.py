from sqlalchemy import Column, String, DateTime, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
from app.database import Base
from app.models.types import SealedJSON
import uuid


class SystemSettings(Base):
    """System configuration for Linux patch management"""
    __tablename__ = "system_settings"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Patching behavior configuration
    patching_config = Column(JSONB, nullable=False, default={
        "max_concurrent_servers": 5,
        "pre_patch_health_check": True,
        "post_patch_validation": True,
        "automatic_rollback_on_failure": True,
        "max_downtime_minutes": 120
    })

    # General display and UI preferences, validated by GeneralConfig in the admin API
    general_config = Column(JSONB, nullable=False, default={})

    # Backup provider connection (Proxmox VE / PBS). Contains token secrets, so it is encrypted at rest
    backup_config = Column(SealedJSON, nullable=False, default={})

    # Notification settings (webhook URL). The URL can carry a token, so it is encrypted at rest
    notification_config = Column(SealedJSON, nullable=False, default={})

    # Web access (HTTP/HTTPS) settings. Holds the TLS private key, so it is encrypted at rest
    tls_config = Column(SealedJSON, nullable=False, default={})

    # Off-host copy of database dumps and audit archives over SFTP. Holds the SSH key or password, so encrypted at rest
    offsite_config = Column(SealedJSON, nullable=False, default={})

    # Auto-approval policies
    auto_approve_critical = Column(String(1), nullable=False, default="f")  # Auto-approve critical security patches
    require_approval_for_kernel = Column(String(1), nullable=False, default="t")  # Require approval for kernel updates

    # Maintenance windows (UTC)
    maintenance_window_start = Column(String(50), nullable=True)  # "02:00 UTC" format
    maintenance_window_end = Column(String(50), nullable=True)    # "04:00 UTC" format

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
