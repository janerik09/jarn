"""
Who is calling, and what they may do. Viewers read. Operators change things day to day. Admins manage users,
settings, certificates and notifications.
"""

from typing import Optional

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.future import select
from starlette.requests import HTTPConnection

from app.database import get_db
from app.models import User
from app.security import decode_token

LEVELS = {"viewer": 1, "operator": 2, "admin": 3}
READ, WRITE, ADMIN = 1, 2, 3


def _bearer(conn: HTTPConnection) -> Optional[str]:
    header = conn.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    # Browsers cannot send headers when opening a WebSocket, so the job monitor passes the token in the URL
    return conn.query_params.get("token")


async def current_user(conn: HTTPConnection, db: AsyncSession = Depends(get_db)) -> User:
    token = _bearer(conn)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue")
    payload = decode_token(token)
    if not payload or payload.get("type") != "access" or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Your session has expired. Sign in again.")
    user = (await db.execute(
        select(User).options(selectinload(User.roles)).where(User.id == payload["sub"])
    )).scalar_one_or_none()
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in to continue")
    return user


def level_of(user: User) -> int:
    return max((LEVELS.get(role.name, 0) for role in user.roles), default=0)


def guard(read: int = READ, write: int = WRITE):
    """Dependency that requires the given role level: `read` for GET requests, `write` for everything else."""
    async def check(conn: HTTPConnection, user: User = Depends(current_user)) -> User:
        method = conn.scope.get("method", "GET")
        needed = read if method in ("GET", "HEAD", "OPTIONS") or conn.scope["type"] == "websocket" else write
        if level_of(user) < needed:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Your role does not allow this action")
        return user
    return check


def admin_only():
    return guard(read=ADMIN, write=ADMIN)
