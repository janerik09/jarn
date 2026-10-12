from sqlalchemy import Column, String, DateTime, Enum as SQLEnum, Text, Date, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from enum import Enum
import uuid


class PatchType(str, Enum):
    security = "security"
    bugfix = "bugfix"
    enhancement = "enhancement"
    critical = "critical"


class OSFamily(str, Enum):
    debian = "debian"
    rhel = "rhel"


class Patch(Base):
    __tablename__ = "patches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    package_name = Column(String(255), nullable=False, index=True)
    version = Column(String(100), nullable=False)
    os_family = Column(SQLEnum(OSFamily, name="os_family"), nullable=False, index=True)
    patch_type = Column(SQLEnum(PatchType, name="patch_type"), nullable=False, index=True)

    release_date = Column(Date, nullable=True)
    description = Column(Text, nullable=True)

    # CVE IDs for security patches
    cve_ids = Column(ARRAY(String), nullable=True, default=[])

    # Additional metadata
    metadata_ = Column(JSONB, nullable=True, default={})

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    job_patches = relationship("PatchJobPatch", back_populates="patch")


class PatchJobPatch(Base):
    """Many-to-many association between jobs and patches"""
    __tablename__ = "patch_job_patches"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("patch_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    patch_id = Column(UUID(as_uuid=True), ForeignKey("patches.id"), nullable=False, index=True)

    # Relationships
    patch = relationship("Patch", back_populates="job_patches")
