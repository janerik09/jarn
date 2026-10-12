from sqlalchemy import Column, String, DateTime, Enum as SQLEnum, Text, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from enum import Enum
import uuid


class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    cancelled = "cancelled"


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    rolled_back = "rolled_back"
    cancelled = "cancelled"


class ExecutionStatus(str, Enum):
    pending = "pending"
    executing = "executing"
    success = "success"
    failed = "failed"
    rolled_back = "rolled_back"
    skipped = "skipped"


class JobPriority(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class PatchJob(Base):
    __tablename__ = "patch_jobs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    status = Column(SQLEnum(JobStatus, name="job_status"), nullable=False, default=JobStatus.pending, index=True)

    # Creator and scheduler
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    schedule_id = Column(UUID(as_uuid=True), ForeignKey("schedules.id"), nullable=True)
    patch_group_id = Column(UUID(as_uuid=True), ForeignKey("patch_groups.id"), nullable=True)

    # Execution timing
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)

    # Patches (can be UUIDs or bundle names) and servers
    patch_ids = Column(JSONB, nullable=False, default=[])  # Changed to JSONB to support bundle names
    server_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=[])

    # Job configuration
    priority = Column(SQLEnum(JobPriority, name="job_priority"), nullable=False, default=JobPriority.medium)
    dry_run = Column(Boolean, nullable=False, default=False)
    auto_rollback = Column(Boolean, nullable=False, default=False)
    backup_before = Column(Boolean, nullable=False, default=False)  # Back up each server before patching
    restore_on_failure = Column(Boolean, nullable=False, default=False)  # Restore from backup if rollback fails
    reboot_after = Column(Boolean, nullable=False, default=False)  # Reboot servers after patches if needed
    graceful_reboot = Column(Boolean, nullable=False, default=True)  # Graceful reboot vs immediate

    # Change Management Integration
    change_id = Column(String(100), nullable=True)  # Ticket ID (e.g., CHG-12345)
    change_management_system = Column(String(50), nullable=True)  # e.g., ServiceNow, Jira

    # Approval tracking
    approval_status = Column(SQLEnum(ApprovalStatus, name="approval_status"),
                            nullable=False, default=ApprovalStatus.pending)
    approver_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approval_notes = Column(Text, nullable=True)
    approved_at = Column(DateTime, nullable=True)

    # Business justification
    business_justification = Column(Text, nullable=True)
    risk_level = Column(String(50), nullable=True)  # low, medium, high, critical

    # Additional metadata
    metadata_ = Column(JSONB, nullable=True, default={})

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    created_by_user = relationship("User", foreign_keys=[created_by_id], backref="created_jobs")
    approver_user = relationship("User", foreign_keys=[approver_id], backref="approved_jobs")
    executions = relationship("PatchJobExecution", back_populates="job", cascade="all, delete-orphan")
    schedule = relationship("Schedule", back_populates="jobs")
    patch_group = relationship("PatchGroup", back_populates="jobs")
    job_patches = relationship("PatchJobPatch", backref="job")


class PatchJobExecution(Base):
    """Tracks execution of a patch job on a specific server"""
    __tablename__ = "patch_job_executions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    job_id = Column(UUID(as_uuid=True), ForeignKey("patch_jobs.id", ondelete="CASCADE"), nullable=False, index=True)
    server_id = Column(UUID(as_uuid=True), ForeignKey("servers.id"), nullable=False, index=True)

    status = Column(SQLEnum(ExecutionStatus, name="execution_status"), nullable=False, default=ExecutionStatus.pending, index=True)

    # Timing
    execution_start = Column(DateTime, nullable=True)
    execution_end = Column(DateTime, nullable=True)

    # Output and errors
    output = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)

    # Rollback tracking
    rollback_needed = Column(Boolean, nullable=False, default=False)
    rollback_performed = Column(Boolean, nullable=False, default=False)

    # Additional metadata
    metadata_ = Column(JSONB, nullable=True, default={})

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    job = relationship("PatchJob", back_populates="executions")
    server = relationship("Server", back_populates="patch_job_executions")


class Schedule(Base):
    """Maintenance windows and scheduled patch jobs"""
    __tablename__ = "schedules"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)

    # Cron scheduling
    cron_expression = Column(String(100), nullable=False)  # e.g., "0 2 * * 0"
    timezone = Column(String(100), nullable=False, default="UTC")
    duration_minutes = Column(String, nullable=False)  # How long the window lasts

    # Target servers and patches
    server_ids = Column(ARRAY(UUID(as_uuid=True)), nullable=False, default=[])

    # Patch filters (e.g., only security patches, specific types)
    patch_filters = Column(JSONB, nullable=True, default={})

    # Status and creator
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)  # No login yet, so no creator

    # Additional metadata
    metadata_ = Column(JSONB, nullable=True, default={})

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    created_by_user = relationship("User", back_populates="schedules")
    jobs = relationship("PatchJob", back_populates="schedule")
