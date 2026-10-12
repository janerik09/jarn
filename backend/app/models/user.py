from sqlalchemy import Column, String, Boolean, DateTime, ForeignKey, Enum as SQLEnum, Table
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.types import SealedJSON
from enum import Enum
import uuid


class AuthType(str, Enum):
    local = "local"
    ldap = "ldap"
    oauth = "oauth"


# Association table for User-Role many-to-many relationship
user_roles = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', UUID(as_uuid=True), ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
    Column('role_id', UUID(as_uuid=True), ForeignKey('roles.id', ondelete='CASCADE'), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(255), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=True)  # Nullable for LDAP/OAuth users
    auth_type = Column(SQLEnum(AuthType, name="auth_type"), nullable=False, default=AuthType.local)
    is_active = Column(Boolean, nullable=False, default=True)
    # Optional TOTP multi-factor authentication. The secret and recovery code hashes are encrypted at rest
    mfa_enabled = Column(Boolean, nullable=False, default=False)
    mfa = Column(SealedJSON, nullable=False, default={})
    last_login_at = Column(DateTime, nullable=True)
    metadata_ = Column(JSONB, nullable=True, default={})
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    roles = relationship("Role", secondary=user_roles, back_populates="users")
    audit_logs = relationship("AuditLog", back_populates="actor")
    schedules = relationship("Schedule", back_populates="created_by_user")


class Role(Base):
    __tablename__ = "roles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(String(1000), nullable=True)
    permissions = Column(JSONB, nullable=False, default={})
    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    # Relationships
    users = relationship("User", secondary=user_roles, back_populates="roles")


# Default permissions for each role
DEFAULT_ROLES = {
    "admin": {
        "name": "admin",
        "description": "Full system access",
        "permissions": {
            "servers:create": True,
            "servers:read": True,
            "servers:update": True,
            "servers:delete": True,
            "patches:read": True,
            "patches:scan": True,
            "jobs:create": True,
            "jobs:execute": True,
            "jobs:cancel": True,
            "jobs:rollback": True,
            "schedules:create": True,
            "schedules:update": True,
            "schedules:delete": True,
            "audit:read": True,
            "audit:export": True,
            "users:manage": True,
            "roles:manage": True,
        }
    },
    "operator": {
        "name": "operator",
        "description": "Can manage patch jobs and view servers",
        "permissions": {
            "servers:read": True,
            "patches:read": True,
            "patches:scan": True,
            "jobs:create": True,
            "jobs:execute": True,
            "jobs:cancel": True,
            "jobs:rollback": True,
            "schedules:read": True,
            "audit:read": True,
        }
    },
    "viewer": {
        "name": "viewer",
        "description": "Read-only access",
        "permissions": {
            "servers:read": True,
            "patches:read": True,
            "jobs:read": True,
            "schedules:read": True,
            "audit:read": True,
        }
    }
}
