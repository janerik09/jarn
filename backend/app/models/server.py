from sqlalchemy import Column, String, DateTime, Enum as SQLEnum, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, INET, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.types import SealedJSON
from enum import Enum
import uuid


class OSType(str, Enum):
    ubuntu = "ubuntu"
    debian = "debian"
    centos = "centos"
    redhat = "redhat"
    fedora = "fedora"
    rocky = "rocky"
    almalinux = "almalinux"


class AuthMethod(str, Enum):
    ssh_key = "ssh_key"
    ssh_password = "ssh_password"


class ConnectivityStatus(str, Enum):
    healthy = "healthy"
    degraded = "degraded"
    unreachable = "unreachable"
    unknown = "unknown"


class Server(Base):
    __tablename__ = "servers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hostname = Column(String(255), unique=True, nullable=False, index=True)
    ip_address = Column(INET, nullable=True)
    os_type = Column(SQLEnum(OSType, name="os_type"), nullable=False)
    os_version = Column(String(100), nullable=True)
    auth_method = Column(SQLEnum(AuthMethod, name="auth_method"), nullable=False)
    port = Column(String(10), nullable=True, default="22")  # SSH port

    # Credentials (username, password, private_key) encrypted at rest with AES-256-GCM via SealedJSON
    credentials = Column(SealedJSON, nullable=False, default={})

    connectivity_status = Column(SQLEnum(ConnectivityStatus, name="connectivity_status"),
                                default=ConnectivityStatus.unknown,
                                nullable=False)
    last_check_at = Column(DateTime, nullable=True)
    last_patch_scan_at = Column(DateTime, nullable=True)

    # Custom metadata: tags, location, datacenter, etc.
    metadata_ = Column(JSONB, nullable=True, default={})

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    patch_job_executions = relationship("PatchJobExecution", back_populates="server", cascade="all, delete-orphan")
    patch_statuses = relationship("ServerPatchStatus", back_populates="server", cascade="all, delete-orphan")


class ServerPatchStatus(Base):
    __tablename__ = "server_patch_status"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id"), nullable=False, index=True)
    patch_id = Column(UUID(as_uuid=True), nullable=False, index=True)

    # Status: available, installed, pending
    status = Column(String(50), nullable=False, index=True)
    last_scan_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    server = relationship("Server", back_populates="patch_statuses")

    __table_args__ = (
        # Create a composite unique index
    )
