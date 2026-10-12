"""User administration. Admins only."""

import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import admin_only, current_user, level_of
from app.api.v1.auth import _user_view
from app.database import get_db
from app.logging_config import get_logger
from app.models import User
from app.models.audit import SessionToken
from app.models.user import Role
from app.security import hash_password

logger = get_logger(__name__)
router = APIRouter(dependencies=[Depends(admin_only())])

ROLE_NAMES = ("admin", "operator", "viewer")


class UserCreate(BaseModel):
    username: str
    password: str
    email: Optional[EmailStr] = None
    role: str = "viewer"


class UserUpdate(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    email: Optional[EmailStr] = None
    password: Optional[str] = None  # Reset by an admin


async def _role(db: AsyncSession, name: str) -> Role:
    if name not in ROLE_NAMES:
        raise HTTPException(status_code=400, detail=f"Role must be one of {', '.join(ROLE_NAMES)}")
    role = (await db.execute(select(Role).where(Role.name == name))).scalar_one_or_none()
    if role is None:
        raise HTTPException(status_code=500, detail=f"The {name} role is missing from the database")
    return role


async def _active_admins(db: AsyncSession) -> int:
    users = (await db.execute(select(User).options(selectinload(User.roles)).where(User.is_active.is_(True)))).scalars().all()
    return sum(1 for u in users if any(r.name == "admin" for r in u.roles))


@router.get("")
async def list_users(db: AsyncSession = Depends(get_db)):
    users = (await db.execute(select(User).options(selectinload(User.roles)).order_by(User.username))).scalars().all()
    # Deleted accounts remain in the database for the audit trail, but are not listed
    return {"users": [_user_view(u) for u in users if not (u.metadata_ or {}).get("deleted_at")]}


@router.post("", status_code=201)
async def create_user(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    if not payload.username.strip():
        raise HTTPException(status_code=400, detail="Enter a username")
    if (await db.execute(select(User.id).where(User.username == payload.username.strip()))).first():
        raise HTTPException(status_code=409, detail="That username is already taken")
    role = await _role(db, payload.role)
    user = User(username=payload.username.strip(), email=payload.email, password_hash=hash_password(payload.password),
                is_active=True, roles=[role])
    db.add(user)
    await db.commit()
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.username == payload.username.strip()))).scalar_one()
    logger.info("user_created", username=user.username, role=payload.role)
    return _user_view(user)


@router.put("/{user_id}")
async def update_user(user_id: str, payload: UserUpdate, actor: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    was_admin = any(r.name == "admin" for r in user.roles)
    from app.services import audit as _audit
    _before = _audit.copy_json({"email": user.email, "is_active": user.is_active, "roles": sorted(r.name for r in user.roles)})
    if payload.role is not None:
        new_role = await _role(db, payload.role)
        if was_admin and payload.role != "admin" and await _active_admins(db) <= 1:
            raise HTTPException(status_code=400, detail="At least one active admin is needed")
        user.roles = [new_role]
    if payload.is_active is not None:
        if payload.is_active is False and was_admin and await _active_admins(db) <= 1:
            raise HTTPException(status_code=400, detail="At least one active admin is needed")
        if payload.is_active is False and user.id == actor.id:
            raise HTTPException(status_code=400, detail="You cannot disable your own account")
        user.is_active = payload.is_active
    if payload.email is not None:
        user.email = payload.email
    if payload.password:
        user.password_hash = hash_password(payload.password)
    await db.commit()
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user_id))).scalar_one()
    _after = _audit.copy_json({"email": user.email, "is_active": user.is_active, "roles": sorted(r.name for r in user.roles)})
    await _audit.record('config.update', category='users', resource_type='user', resource_id=user_id,
                        details={'changes': _audit.diff(_before, _after), 'password_reset': bool(payload.password)})
    logger.info("user_updated", username=user.username)
    return _user_view(user)


@router.delete("/{user_id}")
async def delete_user(user_id: str, actor: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Removes a user's access and personal data. The row stays, because the audit trail refers to it: deleting it
    would break the record of what that user did. The name is freed for reuse."""
    from sqlalchemy import update
    from app.services import audit as _audit
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == actor.id:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if any(r.name == "admin" for r in user.roles) and user.is_active and await _active_admins(db) <= 1:
        raise HTTPException(status_code=400, detail="At least one active admin is needed")
    old_name = user.username
    user.username = f"deleted-{uuid.uuid4().hex[:12]}"
    user.email = None
    user.password_hash = None
    user.mfa_enabled = False
    user.mfa = {}
    user.is_active = False
    user.roles = []
    user.metadata_ = {"deleted_at": datetime.utcnow().isoformat(), "deleted_by": actor.username}
    await db.execute(update(SessionToken).where(SessionToken.user_id == user.id, SessionToken.revoked_at.is_(None))
                     .values(revoked_at=datetime.utcnow(), revoked_reason="user deleted"))
    await db.commit()
    await _audit.record("user.delete", category="users", resource_type="user", resource_id=user_id,
                        details={"former_username": old_name, "deleted_by": actor.username})
    logger.info("user_deleted", user_id=user_id)
    return {"deleted": True}
