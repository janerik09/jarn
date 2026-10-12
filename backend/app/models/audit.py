from sqlalchemy import BigInteger, Column, String, DateTime, Enum as SQLEnum, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, INET, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from enum import Enum
import uuid


class AuditStatus(str, Enum):
    success = "success"
    failure = "failure"


class AuditLog(Base):
    """Immutable append-only audit log for compliance"""
    __tablename__ = "audit_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Who did it
    actor_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)

    # What they did
    action = Column(String(100), nullable=False, index=True)  # e.g., create_job, execute_patch
    resource_type = Column(String(100), nullable=True, index=True)  # e.g., patch_job, server; empty for system events
    resource_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    # Changes and details
    changes = Column(JSONB, nullable=True, default={})  # Before/after snapshots
    status = Column(SQLEnum(AuditStatus, name="audit_status"), nullable=False, default=AuditStatus.success)
    reason = Column(Text, nullable=True)  # Why it was done or error message

    # Request context
    ip_address = Column(INET, nullable=True)
    user_agent = Column(String(500), nullable=True)

    # Structured fields for review and search
    seq = Column(BigInteger, nullable=False, unique=True, index=True)  # Insertion order, used by the hash chain
    category = Column(String(50), nullable=True, index=True)  # auth, access, config, job, ssh, backup, system, ...
    outcome = Column(String(20), nullable=True, index=True)  # success, failure, denied, info
    source = Column(String(20), nullable=False, default="api")  # api, worker, scheduler, system
    correlation_id = Column(String(64), nullable=True, index=True)  # Ties related events together, e.g. one job
    actor_name = Column(String(255), nullable=True)  # Username, including failed sign-in attempts
    details = Column(JSONB, nullable=True)  # Structured detail: changes, commands, reasons
    prev_hash = Column(String(64), nullable=True)
    row_hash = Column(String(64), nullable=True)

    # Immutable timestamp - set at creation only
    created_at = Column(DateTime, server_default=func.now(), nullable=False, index=True)

    # Relationships
    actor = relationship("User", back_populates="audit_logs")


class SessionToken(Base):
    """Track active sessions for logout and session management"""
    __tablename__ = "session_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Token (jti claim)
    token_jti = Column(String(500), unique=True, nullable=False)

    # Expiry
    expires_at = Column(DateTime, nullable=False, index=True)

    # Revocation (for logout)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(255), nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
