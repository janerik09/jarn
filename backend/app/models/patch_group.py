from sqlalchemy import Column, String, DateTime, Enum as SQLEnum, Text, ForeignKey, Boolean
from sqlalchemy.dialects.postgresql import UUID, JSONB
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


class PatchGroup(Base):
    """Logical grouping of patches for change management"""
    __tablename__ = "patch_groups"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    # Patches in this group (bundle names or IDs)
    patch_ids = Column(JSONB, nullable=False, default=[])

    # Target servers for this group
    server_ids = Column(JSONB, nullable=False, default=[])

    # Change Management Integration
    change_id = Column(String(100), nullable=True)  # Ticket ID from change management system
    change_management_system = Column(String(50), nullable=True)  # e.g., "ServiceNow", "Jira", "Rally"

    # Approval tracking
    approval_status = Column(SQLEnum(ApprovalStatus, name="approval_status"),
                            nullable=False, default=ApprovalStatus.pending)
    approver_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    approved_at = Column(DateTime, nullable=True)
    approval_notes = Column(Text, nullable=True)

    # Business information
    business_justification = Column(Text, nullable=True)
    risk_level = Column(String(50), nullable=True)  # low, medium, high, critical
    maintenance_window = Column(String(255), nullable=True)  # e.g., "Saturday 2AM-4AM"

    # Status
    is_active = Column(Boolean, nullable=False, default=True)
    created_by_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # Timestamps
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
    scheduled_date = Column(DateTime, nullable=True)

    # Additional metadata
    metadata_ = Column(JSONB, nullable=True, default={})

    # Relationships
    created_by_user = relationship("User", foreign_keys=[created_by_id], backref="patch_groups_created")
    approver_user = relationship("User", foreign_keys=[approver_id], backref="patch_groups_approved")
    jobs = relationship("PatchJob", back_populates="patch_group")
