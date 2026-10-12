"""
Sign-in, sessions, the signed-in user's own account, and optional authenticator-app MFA.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import current_user, level_of
from app.database import get_db
from app.logging_config import get_logger
from app.models import User
from app.models.audit import SessionToken
from app.models.user import AuthType, Role
from app.security import (create_access_token, create_refresh_token, decode_token,
                          hash_password, verify_password)
from app.config import settings
from app.services import audit, ratelimit, totp

logger = get_logger(__name__)
router = APIRouter()

ACCESS_MINUTES = 15
RECOVERY_CODES = 10
INVALID_LOGIN = "Invalid username or password"
# The refresh token lives in an HttpOnly cookie that only the auth endpoints receive, so scripts cannot read it
REFRESH_COOKIE = "jarn_refresh"
REFRESH_COOKIE_PATH = "/api/v1/auth"
REFRESH_SECONDS = 7 * 24 * 3600
REFRESH_GRACE_SECONDS = 30
LOGIN_IP_LIMIT = 30  # sign-in attempts per address
LOGIN_IP_WINDOW_SECONDS = 15 * 60


class SetupRequest(BaseModel):
    username: str
    password: str
    email: Optional[EmailStr] = None

    @field_validator("username")
    @classmethod
    def _username(cls, v: str) -> str:
        v = v.strip()
        if not v or len(v) > 255:
            raise ValueError("Enter a username")
        return v


class LoginRequest(BaseModel):
    username: str
    password: str
    code: Optional[str] = None  # Authenticator code or recovery code, when MFA is on


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class CodeRequest(BaseModel):
    code: str


class DisableMfaRequest(BaseModel):
    password: str


def _recovery_hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").upper().encode()).hexdigest()


def _client_ip(http: Request) -> str:
    # nginx sets X-Real-IP from the connecting address; fall back to the socket peer
    return http.headers.get("x-real-ip") or (http.client.host if http.client else "unknown")


def _is_https(http: Request) -> bool:
    return http.url.scheme == "https" or http.headers.get("x-forwarded-proto") == "https"


def _set_refresh_cookie(response: Response, http: Request, token: str) -> None:
    response.set_cookie(
        REFRESH_COOKIE, token, max_age=REFRESH_SECONDS, path=REFRESH_COOKIE_PATH,
        httponly=True, samesite="strict", secure=_is_https(http),
    )


def _clear_refresh_cookie(response: Response, http: Request) -> None:
    response.delete_cookie(REFRESH_COOKIE, path=REFRESH_COOKIE_PATH, httponly=True,
                           samesite="strict", secure=_is_https(http))


def _signed_out(http: Request) -> JSONResponse:
    """A 401 that also clears the refresh cookie. Headers set on an injected Response are lost when raising, so this returns one."""
    response = JSONResponse(status_code=401, content={"detail": "Sign in again"})
    _clear_refresh_cookie(response, http)
    return response


async def _issue_tokens(db: AsyncSession, user: User, sign_in: bool = True) -> dict:
    """New access and refresh tokens, with the refresh token recorded as a session so it can be revoked."""
    subject = {"sub": str(user.id), "username": user.username, "type": "access"}
    access = create_access_token(subject, expires_delta=timedelta(minutes=ACCESS_MINUTES))
    refresh = create_refresh_token({"sub": str(user.id), "username": user.username})
    payload = decode_token(refresh)
    db.add(SessionToken(
        user_id=user.id,
        token_jti=payload["jti"] if payload and "jti" in payload else uuid.uuid4().hex,
        expires_at=datetime.utcfromtimestamp(payload["exp"]) if payload else datetime.utcnow() + timedelta(days=7),
    ))
    if sign_in:
        user.last_login_at = datetime.utcnow()
    await db.commit()
    return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}


def _user_view(user: User) -> dict:
    roles = sorted(role.name for role in user.roles)
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "roles": roles,
        "level": level_of(user),
        "is_active": user.is_active,
        "mfa_enabled": bool(user.mfa_enabled),
    }


async def _user_by_name(db: AsyncSession, username: str) -> Optional[User]:
    return (await db.execute(
        select(User).options(selectinload(User.roles)).where(User.username == username)
    )).scalar_one_or_none()


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)):
    """Tells the sign-in page whether the first admin account still needs to be created."""
    count = (await db.execute(select(User.id).limit(1))).first()
    return {"setup_required": count is None, "mfa_available": True}


@router.post("/setup", status_code=201)
async def first_run_setup(request: SetupRequest, http: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Creates the first admin account. Only works while there are no users."""
    if (await db.execute(select(User.id).limit(1))).first() is not None:
        raise HTTPException(status_code=409, detail="Setup is already complete. Sign in instead.")
    admin_role = (await db.execute(select(Role).where(Role.name == "admin"))).scalar_one_or_none()
    if admin_role is None:
        raise HTTPException(status_code=500, detail="The admin role is missing from the database")
    user = User(username=request.username, email=request.email, password_hash=hash_password(request.password),
                is_active=True, roles=[admin_role])
    db.add(user)
    await db.commit()
    user = await _user_by_name(db, request.username)
    logger.info("first_admin_created", username=user.username)
    tokens = await _issue_tokens(db, user)
    _set_refresh_cookie(response, http, tokens.pop("refresh_token"))
    return {**tokens, "user": _user_view(user)}


LOCKOUT_ATTEMPTS = 10
LOCKOUT_MINUTES = 15


async def _recent_failed_logins(db: AsyncSession, username: str) -> int:
    from datetime import datetime as _dt
    from sqlalchemy import func
    from app.models.audit import AuditLog
    since = _dt.utcnow() - timedelta(minutes=LOCKOUT_MINUTES)
    return (await db.execute(
        select(func.count()).select_from(AuditLog).where(
            AuditLog.action == "POST /api/v1/auth/login",
            AuditLog.outcome == "failure",
            AuditLog.actor_name == username,
            AuditLog.created_at >= since,
        )
    )).scalar_one()


@router.post("/login")
async def login(request: LoginRequest, http: Request, response: Response, db: AsyncSession = Depends(get_db)):
    from app.services import sso
    # Spreads guessing across many accounts from one address; the per-account lockout below catches the rest
    if not await ratelimit.allow(f"login:ip:{_client_ip(http)}", LOGIN_IP_LIMIT, LOGIN_IP_WINDOW_SECONDS):
        raise HTTPException(status_code=429, detail="Too many sign-in attempts from this address. Try again later.")
    username = request.username.strip()
    if await _recent_failed_logins(db, username) >= LOCKOUT_ATTEMPTS:
        raise HTTPException(status_code=429, detail=f"Too many failed sign-in attempts. Try again in {LOCKOUT_MINUTES} minutes.")
    config = await sso.load_config(db)
    user = await _user_by_name(db, username)

    if user is not None and user.auth_type == AuthType.oauth:
        raise HTTPException(status_code=400, detail="This account signs in with single sign-on. Use the sign-in button for your provider.")

    if user is not None and user.auth_type == AuthType.local:
        if not sso.local_login_allowed(config, username):
            raise HTTPException(status_code=403, detail="Password sign-in is turned off. Use single sign-on.")
        if not verify_password(request.password, user.password_hash or ""):
            logger.warning("login_failed", username=username)
            raise HTTPException(status_code=401, detail=INVALID_LOGIN)
    else:
        # Directory accounts, and names not yet known locally when LDAP is on
        if not sso.ldap_enabled(config):
            logger.warning("login_failed", username=username)
            raise HTTPException(status_code=401, detail=INVALID_LOGIN)
        try:
            user = await sso.ldap_sign_in(db, username, request.password, config.get("ldap") or {})
        except sso.SignInDenied as e:
            logger.warning("login_ldap_failed", username=username, reason=str(e))
            await audit.record("auth.sso.login", category="auth", outcome="failure", actor_name=username,
                               source="ldap", reason=str(e))
            raise HTTPException(status_code=401, detail=str(e) if "group" in str(e) or "disabled" in str(e) else INVALID_LOGIN)
        except Exception as e:
            logger.error("ldap_unavailable", error=str(e))
            raise HTTPException(status_code=503, detail="The directory service could not be reached. Try again later.")

    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account is disabled")
    if user.mfa_enabled:
        if not request.code:
            return {"mfa_required": True}
        if not _check_mfa_code(user, request.code):
            logger.warning("login_mfa_failed", username=user.username)
            raise HTTPException(status_code=401, detail="The code is not valid. Check your authenticator app and try again.")
    logger.info("login_success", user_id=str(user.id))
    tokens = await _issue_tokens(db, user)
    _set_refresh_cookie(response, http, tokens.pop("refresh_token"))
    return {**tokens, "user": _user_view(user), "mfa_required": False}


def _check_mfa_code(user: User, code: str) -> bool:
    mfa = dict(user.mfa or {})
    if totp.verify(mfa.get("secret", ""), code):
        return True
    recovery = mfa.get("recovery", [])
    digest = _recovery_hash(code)
    if digest in recovery:
        # Each recovery code works once
        mfa["recovery"] = [h for h in recovery if h != digest]
        user.mfa = mfa
        return True
    return False


@router.post("/refresh")
async def refresh(http: Request, response: Response, db: AsyncSession = Depends(get_db)):
    """Exchanges the refresh cookie for a new access token and rotates the refresh token.

    A refresh token that was already rotated or revoked is being replayed. That means it was copied, so every
    session of that user is revoked and they must sign in again.
    """
    token = http.cookies.get(REFRESH_COOKIE)
    payload = decode_token(token) if token else None
    if not payload or payload.get("type") != "refresh" or "jti" not in payload:
        return _signed_out(http)
    session = (await db.execute(select(SessionToken).where(SessionToken.token_jti == payload["jti"]))).scalar_one_or_none()
    if session is None:
        return _signed_out(http)
    if session.revoked_at is not None:
        # Two open tabs can refresh at the same moment, so a replay just after rotation is refused without
        # touching the cookie, which the other tab has already replaced
        if session.revoked_reason == "rotated" and session.revoked_at >= datetime.utcnow() - timedelta(seconds=REFRESH_GRACE_SECONDS):
            raise HTTPException(status_code=401, detail="Sign in again")
        if session.revoked_reason == "rotated":
            logger.warning("refresh_token_reuse", user_id=str(session.user_id))
            from sqlalchemy import update
            await db.execute(update(SessionToken).where(
                SessionToken.user_id == session.user_id, SessionToken.revoked_at.is_(None)
            ).values(revoked_at=datetime.utcnow(), revoked_reason="reuse detected"))
            await db.commit()
        return _signed_out(http)
    user = await _user_by_name(db, payload.get("username", ""))
    if user is None or not user.is_active or str(user.id) != payload.get("sub"):
        return _signed_out(http)
    session.revoked_at = datetime.utcnow()
    session.revoked_reason = "rotated"
    tokens = await _issue_tokens(db, user, sign_in=False)
    _set_refresh_cookie(response, http, tokens.pop("refresh_token"))
    return {**tokens, "user": _user_view(user)}


@router.post("/logout")
async def logout(http: Request, response: Response, db: AsyncSession = Depends(get_db)):
    payload = decode_token(http.cookies.get(REFRESH_COOKIE) or "")
    if payload and "jti" in payload:
        session = (await db.execute(select(SessionToken).where(SessionToken.token_jti == payload["jti"]))).scalar_one_or_none()
        if session and session.revoked_at is None:
            session.revoked_at = datetime.utcnow()
            session.revoked_reason = "logout"
            await db.commit()
    _clear_refresh_cookie(response, http)
    return {"message": "Signed out"}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return _user_view(user)


@router.post("/password")
async def change_password(request: PasswordChange, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not verify_password(request.current_password, user.password_hash or ""):
        raise HTTPException(status_code=400, detail="The current password is not correct")
    if not request.new_password:
        raise HTTPException(status_code=400, detail="Enter a new password")
    user.password_hash = hash_password(request.new_password)
    await db.commit()
    return {"message": "Password changed"}


@router.post("/mfa/setup")
async def mfa_setup(user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    """Starts authenticator-app setup. Nothing changes until the first code is confirmed."""
    if user.mfa_enabled:
        raise HTTPException(status_code=409, detail="MFA is already on. Turn it off first to set it up again.")
    secret = totp.new_secret()
    user.mfa = {"pending_secret": secret}
    await db.commit()
    return {"secret": secret, "uri": totp.provisioning_uri(secret, user.username), "issuer": totp.ISSUER}


@router.post("/mfa/enable")
async def mfa_enable(request: CodeRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    pending = (user.mfa or {}).get("pending_secret")
    if not pending:
        raise HTTPException(status_code=400, detail="Start the setup first")
    if not totp.verify(pending, request.code):
        raise HTTPException(status_code=400, detail="The code is not valid. Check the time on your phone and try again.")
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(RECOVERY_CODES)]
    user.mfa = {"secret": pending, "recovery": [_recovery_hash(c) for c in codes]}
    user.mfa_enabled = True
    await db.commit()
    logger.info("mfa_enabled", username=user.username)
    return {"enabled": True, "recovery_codes": codes}


@router.post("/mfa/disable")
async def mfa_disable(request: DisableMfaRequest, user: User = Depends(current_user), db: AsyncSession = Depends(get_db)):
    if not verify_password(request.password, user.password_hash or ""):
        raise HTTPException(status_code=400, detail="The password is not correct")
    user.mfa = {}
    user.mfa_enabled = False
    await db.commit()
    logger.info("mfa_disabled", username=user.username)
    return {"enabled": False}
