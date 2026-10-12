"""
Single sign-on endpoints. Public: the list of sign-in options, and the OpenID Connect start and callback. Admin: the
provider and directory settings, with tests that never reveal stored secrets.
"""

import re
import secrets
from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user
from app.api.v1.auth import _issue_tokens, _set_refresh_cookie
from app.database import get_db
from app.logging_config import get_logger
from app.models import User
from app.models.settings import SystemSettings
from app.services import audit, sso

logger = get_logger(__name__)
public = APIRouter()
admin = APIRouter()
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


# ---------- public ----------

@public.get("/providers")
async def providers(db: AsyncSession = Depends(get_db)):
    """What the sign-in page offers. No secrets."""
    config = await sso.load_config(db)
    return {
        "oidc": [{"id": p["id"], "name": p.get("name") or p["id"]} for p in sso.enabled_providers(config)],
        "ldap": sso.ldap_enabled(config),
        "local_login": bool(config.get("allow_local_login", True)),
    }


def _sso_failure(reason: str) -> RedirectResponse:
    from urllib.parse import quote
    return RedirectResponse(url=f"/?sso=failed&reason={quote(reason[:160])}", status_code=302)


@public.get("/sso/start/{provider_id}")
async def sso_start(provider_id: str, db: AsyncSession = Depends(get_db)):
    import redis.asyncio as redis_async
    from app.config import settings

    config = await sso.load_config(db)
    provider = sso.find_provider(config, provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="That sign-in option is not enabled")
    try:
        discovery = await sso.discover(provider["issuer"])
        url, remembered = sso.build_authorization_url(provider, discovery, config)
    except sso.SignInDenied as e:
        raise HTTPException(status_code=502, detail=str(e))
    client = redis_async.from_url(settings.redis_url, socket_connect_timeout=2)
    try:
        import json as _json
        await client.set(sso.new_state_store_key(remembered["state"]), _json.dumps(remembered), ex=sso.STATE_TTL_SECONDS)
    finally:
        await client.aclose()
    return RedirectResponse(url=url, status_code=302)


@public.get("/sso/callback")
async def sso_callback(request: Request, db: AsyncSession = Depends(get_db),
                       code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
    import redis.asyncio as redis_async
    import json as _json
    from app.config import settings

    if error:
        logger.warning("sso_provider_error", error=error)
        await audit.record("auth.sso.login", category="auth", outcome="failure", source="oidc", reason=f"provider error: {error[:80]}")
        return _sso_failure("The identity provider cancelled the sign-in")
    if not code or not state:
        await audit.record("auth.sso.login", category="auth", outcome="failure", source="oidc", reason="incomplete response")
        return _sso_failure("The sign-in response was incomplete")
    client = redis_async.from_url(settings.redis_url, socket_connect_timeout=2)
    try:
        raw = await client.getdel(sso.new_state_store_key(state))  # single use
    finally:
        await client.aclose()
    if raw is None:
        # Unknown, reused or expired state: possibly a replayed or forged response
        await audit.record("auth.sso.login", category="auth", outcome="failure", source="oidc",
                           reason="unknown, reused or expired sign-in state")
        return _sso_failure("This sign-in link has expired. Start again.")
    remembered = _json.loads(raw)
    config = await sso.load_config(db)
    provider = sso.find_provider(config, remembered["provider"])
    if provider is None:
        return _sso_failure("That sign-in option is no longer enabled")
    try:
        discovery = await sso.discover(provider["issuer"])
        claims = await sso.exchange_code_for_claims(provider, discovery, config, code, remembered["verifier"], remembered["nonce"])
        user = await sso.oidc_claims_to_account(db, provider, claims)
    except sso.SignInDenied as e:
        await audit.record("auth.sso.login", category="auth", outcome="failure", source=f"oidc:{provider['id']}", reason=str(e))
        return _sso_failure(str(e))
    except httpx.HTTPError:
        return _sso_failure("The identity provider could not be reached")
    if not user.is_active:
        return _sso_failure("This account is disabled")
    tokens = await _issue_tokens(db, user)
    await audit.record("auth.sso.login", category="auth", outcome="success", actor_id=user.id,
                       actor_name=user.username, source=f"oidc:{provider['id']}")
    response = RedirectResponse(url="/", status_code=302)
    _set_refresh_cookie(response, request, tokens.pop("refresh_token"))
    return response


# ---------- admin ----------

class OidcProviderIn(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    preset: str = "oidc"
    enabled: bool = False
    issuer: str
    client_id: str = Field(min_length=1, max_length=255)
    client_secret: Optional[str] = None  # blank keeps the stored secret
    scopes: str = "openid profile email"
    username_claim: str = "preferred_username"
    groups_claim: str = "groups"
    role_map: Dict[str, str] = {}
    default_role: Optional[str] = None
    allowed_emails: List[str] = []

    @field_validator("id")
    @classmethod
    def _slug(cls, v):
        if not SLUG.match(v):
            raise ValueError("Use lower-case letters, digits and hyphens for the provider id")
        return v

    @field_validator("issuer")
    @classmethod
    def _https(cls, v):
        v = v.strip().rstrip("/")
        if not v.startswith("https://") and not v.startswith("http://localhost") and not v.startswith("http://127.0.0.1"):
            raise ValueError("The issuer must be an https address")
        return v

    @field_validator("role_map")
    @classmethod
    def _roles(cls, v):
        bad = {r for r in v.values() if r not in ("admin", "operator", "viewer")}
        if bad:
            raise ValueError(f"Unknown role in the group mapping: {', '.join(sorted(bad))}")
        return v

    @field_validator("default_role")
    @classmethod
    def _default(cls, v):
        if v not in (None, "", "admin", "operator", "viewer"):
            raise ValueError("Unknown default role")
        return v or None


class LdapIn(BaseModel):
    enabled: bool = False
    host: str = ""
    port: int = 636
    use_ssl: bool = True
    starttls: bool = False
    verify_tls: bool = True
    bind_dn: str = ""
    bind_password: Optional[str] = None  # blank keeps the stored password
    base_dn: str = ""
    user_filter: str = "(&(objectClass=person)(uid={username}))"
    username_attribute: str = "uid"
    email_attribute: str = "mail"
    group_attribute: str = "memberOf"
    group_base_dn: str = ""
    role_map: Dict[str, str] = {}
    default_role: Optional[str] = None

    @field_validator("role_map")
    @classmethod
    def _roles(cls, v):
        bad = {r for r in v.values() if r not in ("admin", "operator", "viewer")}
        if bad:
            raise ValueError(f"Unknown role in the group mapping: {', '.join(sorted(bad))}")
        return v


class SsoSettingsIn(BaseModel):
    public_base_url: str = ""
    allow_local_login: bool = True
    break_glass_usernames: List[str] = []
    providers: List[OidcProviderIn] = []
    ldap: LdapIn = LdapIn()

    @field_validator("public_base_url")
    @classmethod
    def _base(cls, v):
        return v.strip().rstrip("/")


def _public_settings(config: Dict[str, Any]) -> Dict[str, Any]:
    ldap = dict(config.get("ldap") or {})
    return {
        "public_base_url": config.get("public_base_url", ""),
        "allow_local_login": bool(config.get("allow_local_login", True)),
        "break_glass_usernames": list(config.get("break_glass_usernames") or []),
        "redirect_uri": (config.get("public_base_url", "").rstrip("/") + "/api/v1/auth/sso/callback") if config.get("public_base_url") else None,
        "providers": [{**{k: v for k, v in p.items() if k != "client_secret"}, "client_secret_set": bool(p.get("client_secret"))}
                      for p in config.get("providers", [])],
        "ldap": {**{k: v for k, v in ldap.items() if k != "bind_password"}, "bind_password_set": bool(ldap.get("bind_password"))},
        "presets": {k: {kk: vv for kk, vv in v.items()} for k, v in sso.PRESETS.items()},
    }


@admin.get("")
async def get_sso(db: AsyncSession = Depends(get_db)):
    return _public_settings(await sso.load_config(db))


@admin.put("")
async def set_sso(update: SsoSettingsIn, db: AsyncSession = Depends(get_db)):
    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = SystemSettings()
        db.add(row)
    stored = dict(row.sso_config or {})
    stored_providers = {p["id"]: p for p in stored.get("providers", [])}
    ids = [p.id for p in update.providers]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=400, detail="Each sign-in option needs a unique id")
    providers = []
    for p in update.providers:
        data = p.model_dump()
        if not data["client_secret"]:
            data["client_secret"] = (stored_providers.get(p.id) or {}).get("client_secret")
        if data["enabled"] and not data["client_secret"]:
            raise HTTPException(status_code=400, detail=f"Enter the client secret for {p.name}")
        providers.append(data)
    ldap = update.ldap.model_dump()
    if not ldap["bind_password"]:
        ldap["bind_password"] = (stored.get("ldap") or {}).get("bind_password")
    if ldap["enabled"] and not ldap["host"]:
        raise HTTPException(status_code=400, detail="Enter the directory server host name")
    if ldap["enabled"] and not ldap["base_dn"]:
        raise HTTPException(status_code=400, detail="Enter the search base DN")
    if update.public_base_url and not update.public_base_url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="The public address must start with http:// or https://")
    if any(p["enabled"] for p in providers) and not update.public_base_url:
        raise HTTPException(status_code=400, detail="Enter the public address of JÁRN, as your provider will redirect back to it")
    config = {
        "public_base_url": update.public_base_url,
        "allow_local_login": update.allow_local_login,
        "break_glass_usernames": update.break_glass_usernames,
        "providers": providers,
        "ldap": ldap,
    }
    row.sso_config = config
    await db.commit()
    await audit.record("config.update", category="config", resource_type="sso",
                       details={"providers": [(p["id"], p["enabled"]) for p in providers],
                                "ldap_enabled": ldap["enabled"], "allow_local_login": update.allow_local_login,
                                "secrets": "changed fields only; values are never stored in the audit trail"})
    logger.info("sso_settings_updated", providers=len(providers), ldap=ldap["enabled"])
    return _public_settings(config)


@admin.post("/test-oidc/{provider_id}")
async def test_oidc(provider_id: str, db: AsyncSession = Depends(get_db)):
    """Reads the provider's discovery document and checks the endpoints the sign-in uses. Nothing is sent to it."""
    config = await sso.load_config(db)
    provider = next((p for p in config.get("providers", []) if p.get("id") == provider_id), None)
    if provider is None:
        raise HTTPException(status_code=404, detail="Unknown sign-in option")
    try:
        discovery = await sso.discover(provider["issuer"])
        keyset = await sso._jwks(discovery["jwks_uri"])
    except sso.SignInDenied as e:
        raise HTTPException(status_code=400, detail=str(e))
    except httpx.HTTPError as e:
        raise HTTPException(status_code=400, detail=f"The provider could not be reached: {e}")
    return {
        "issuer": discovery["issuer"],
        "authorization_endpoint": discovery["authorization_endpoint"],
        "token_endpoint": discovery["token_endpoint"],
        "signing_keys": len(keyset.keys),
        "redirect_uri": (config.get("public_base_url", "").rstrip("/") + "/api/v1/auth/sso/callback"),
        "note": "Register the redirect URI above with the provider, then sign in with a test account.",
    }


class LdapTestIn(BaseModel):
    username: str = Field(min_length=1, max_length=255)


@admin.post("/test-ldap")
async def test_ldap(body: LdapTestIn, db: AsyncSession = Depends(get_db)):
    """Checks the service account can search the directory and shows what a user resolves to. No password is asked for."""
    config = await sso.load_config(db)
    cfg = config.get("ldap") or {}
    if not cfg.get("host") or not cfg.get("base_dn"):
        raise HTTPException(status_code=400, detail="Save the directory settings first")
    import asyncio
    try:
        return await asyncio.to_thread(sso.ldap_test, body.username, cfg)
    except Exception as e:
        logger.warning("ldap_test_failed", error=str(e))
        raise HTTPException(status_code=400, detail=f"The directory test failed: {type(e).__name__}. Check the host, port, TLS and bind account.")
