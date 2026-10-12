"""
Single sign-on: OpenID Connect (Microsoft Entra ID, Okta, Google, Keycloak and any standards-compliant provider)
and LDAP / Active Directory.

Rules that hold for every provider:
- A user's role comes from the groups the provider reports, on every sign-in. Losing a group removes access at the
  next sign-in, and the roles are replaced rather than added to.
- A user with no mapped role (and no default role) cannot sign in.
- A provider cannot take over a local account. A local account with the same name is refused.
- Tokens are checked for signature, issuer, audience, expiry and nonce. Only asymmetric algorithms are accepted.
"""

import asyncio
import base64
import hashlib
import secrets
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.logging_config import get_logger
from app.models import User
from app.models.settings import SystemSettings
from app.models.user import AuthType, Role

logger = get_logger(__name__)

ROLE_RANK = {"viewer": 1, "operator": 2, "admin": 3}
ALLOWED_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384"]
STATE_TTL_SECONDS = 600
DISCOVERY_TTL_SECONDS = 3600

PRESETS: Dict[str, Dict[str, Any]] = {
    "entra": {"name": "Microsoft Entra ID", "issuer_hint": "https://login.microsoftonline.com/<tenant-id>/v2.0",
              "username_claim": "preferred_username", "groups_claim": "groups",
              "help": "Use the tenant ID or domain in the issuer. Groups are reported as object IDs unless you configure "
                      "group names in the app registration. App roles can be used instead by setting groups claim to roles."},
    "okta": {"name": "Okta", "issuer_hint": "https://<your-domain>/oauth2/default",
             "username_claim": "email", "groups_claim": "groups",
             "help": "Add a Groups claim to the authorization server so that groups appear in the ID token."},
    "google": {"name": "Google", "issuer_hint": "https://accounts.google.com",
               "username_claim": "email", "groups_claim": "",
               "help": "Google does not put group memberships in the sign-in token. Access is granted by email "
                       "address: list the allowed addresses and a default role."},
    "keycloak": {"name": "Keycloak", "issuer_hint": "https://<host>/realms/<realm>",
                 "username_claim": "preferred_username", "groups_claim": "groups",
                 "help": "Add a Group Membership mapper to the client with the claim name groups. Or use realm_access.roles."},
    "oidc": {"name": "OpenID Connect", "issuer_hint": "https://<issuer>",
             "username_claim": "preferred_username", "groups_claim": "groups",
             "help": "Any provider with OpenID Connect discovery."},
}


# ---------- configuration ----------

async def load_config(db: AsyncSession) -> Dict[str, Any]:
    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    return dict(row.sso_config or {}) if row else {}


def enabled_providers(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [p for p in config.get("providers", []) if p.get("enabled")]


def find_provider(config: Dict[str, Any], provider_id: str) -> Optional[Dict[str, Any]]:
    for p in config.get("providers", []):
        if p.get("id") == provider_id and p.get("enabled"):
            return p
    return None


def ldap_enabled(config: Dict[str, Any]) -> bool:
    return bool((config.get("ldap") or {}).get("enabled"))


def local_login_allowed(config: Dict[str, Any], username: str) -> bool:
    """When local sign-in is turned off, only the named break-glass accounts can use a password."""
    if config.get("allow_local_login", True):
        return True
    return username in set(config.get("break_glass_usernames") or [])


# ---------- claims and roles ----------

def claim_value(claims: Dict[str, Any], path: str) -> Any:
    """Reads a claim, including nested ones such as realm_access.roles."""
    value: Any = claims
    for part in (path or "").split("."):
        if not part or not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v) for v in value]


def map_roles(groups: List[str], role_map: Dict[str, str]) -> List[str]:
    """Roles for a set of groups. Matching is case-insensitive on the group value."""
    lowered = {k.lower(): v for k, v in (role_map or {}).items()}
    roles = {lowered[g.lower()] for g in groups if g.lower() in lowered}
    return sorted(r for r in roles if r in ROLE_RANK)


def highest_role(roles: List[str]) -> Optional[str]:
    return max(roles, key=lambda r: ROLE_RANK[r]) if roles else None


def decide_roles(groups: List[str], role_map: Dict[str, str], default_role: Optional[str],
                 email: Optional[str] = None, allowed_emails: Optional[List[str]] = None) -> Optional[str]:
    """The single role to grant, or None when the user may not sign in."""
    if allowed_emails is not None and email and email.lower() in {e.lower() for e in allowed_emails}:
        return default_role or "viewer"
    mapped = highest_role(map_roles(groups, role_map))
    if mapped:
        return mapped
    # The default role applies to anyone who signs in and matches no group. Leave it empty to refuse them instead.
    return default_role if default_role in ROLE_RANK else None


# ---------- account provisioning ----------

class SignInDenied(Exception):
    pass


async def sign_in_account(db: AsyncSession, *, username: str, auth_type: AuthType, source: str,
                          role: Optional[str], email: Optional[str]) -> User:
    """Finds or creates the account for an external sign-in and syncs its single role."""
    if not username:
        raise SignInDenied("The provider did not return a user name")
    user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.username == username))).scalar_one_or_none()
    if user is not None:
        if user.auth_type == AuthType.local:
            raise SignInDenied("A local account already uses this name. Ask an administrator to resolve it.")
        linked = (user.metadata_ or {}).get("sso_source")
        if linked != source:
            raise SignInDenied("This account belongs to another sign-in source")
    if role is None:
        if user is not None:
            # Removed from every group: lose access now, not at the next token expiry
            user.roles = []
            await db.commit()
        raise SignInDenied("Your account is not in a group that has access to JÁRN")
    role_row = (await db.execute(select(Role).where(Role.name == role))).scalar_one_or_none()
    if role_row is None:
        raise SignInDenied(f"The role {role} is missing from the database")
    if user is None:
        user = User(username=username, email=email, password_hash=None, auth_type=auth_type, is_active=True,
                    metadata_={"sso_source": source})
        db.add(user)
        await db.flush()
        user = (await db.execute(select(User).options(selectinload(User.roles)).where(User.id == user.id))).scalar_one()
    if not user.is_active:
        raise SignInDenied("This account is disabled")
    user.roles = [role_row]
    if email:
        user.email = email
    user.metadata_ = {**dict(user.metadata_ or {}), "sso_source": source}
    await db.commit()
    return user


# ---------- OpenID Connect ----------

_discovery_cache: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_jwks_cache: Dict[str, Tuple[float, jwt.PyJWKSet]] = {}


async def discover(issuer: str, client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    issuer = issuer.rstrip("/")
    cached = _discovery_cache.get(issuer)
    if cached and time.time() - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]
    url = f"{issuer}/.well-known/openid-configuration"
    async with (client or httpx.AsyncClient(timeout=10)) as http:
        response = await http.get(url)
    if response.status_code != 200:
        raise SignInDenied(f"The identity provider's discovery document could not be read ({response.status_code})")
    document = response.json()
    if document.get("issuer", "").rstrip("/") != issuer:
        raise SignInDenied("The discovery document names a different issuer")
    _discovery_cache[issuer] = (time.time(), document)
    return document


async def _jwks(jwks_uri: str, client: Optional[httpx.AsyncClient] = None) -> jwt.PyJWKSet:
    cached = _jwks_cache.get(jwks_uri)
    if cached and time.time() - cached[0] < DISCOVERY_TTL_SECONDS:
        return cached[1]
    async with (client or httpx.AsyncClient(timeout=10)) as http:
        response = await http.get(jwks_uri)
    response.raise_for_status()
    keyset = jwt.PyJWKSet.from_dict(response.json())
    _jwks_cache[jwks_uri] = (time.time(), keyset)
    return keyset


def pkce_pair() -> Tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def redirect_uri(config: Dict[str, Any]) -> str:
    base = (config.get("public_base_url") or "").rstrip("/")
    if not base:
        raise SignInDenied("The public address of JÁRN is not set in Single sign-on settings")
    return f"{base}/api/v1/auth/sso/callback"


def build_authorization_url(provider: Dict[str, Any], discovery: Dict[str, Any], config: Dict[str, Any]) -> Tuple[str, Dict[str, str]]:
    """Returns the URL to send the browser to, and the values to remember for the callback."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    params = {
        "response_type": "code",
        "client_id": provider["client_id"],
        "redirect_uri": redirect_uri(config),
        "scope": provider.get("scopes") or "openid profile email",
        "state": state,
        "nonce": nonce,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    from urllib.parse import urlencode
    url = f"{discovery['authorization_endpoint']}?{urlencode(params)}"
    return url, {"state": state, "nonce": nonce, "verifier": verifier, "provider": provider["id"]}


async def exchange_code_for_claims(provider: Dict[str, Any], discovery: Dict[str, Any], config: Dict[str, Any],
                                   code: str, verifier: str, expected_nonce: str,
                                   client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    async with (client or httpx.AsyncClient(timeout=15)) as http:
        response = await http.post(discovery["token_endpoint"], data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri(config),
            "client_id": provider["client_id"],
            "client_secret": provider.get("client_secret") or "",
            "code_verifier": verifier,
        }, headers={"Accept": "application/json"})
    if response.status_code != 200:
        raise SignInDenied("The identity provider rejected the sign-in")
    id_token = response.json().get("id_token")
    if not id_token:
        raise SignInDenied("The identity provider did not return an ID token")
    keyset = await _jwks(discovery["jwks_uri"], client)
    return validate_id_token(id_token, provider, discovery, expected_nonce, keyset)


def validate_id_token(id_token: str, provider: Dict[str, Any], discovery: Dict[str, Any], expected_nonce: str,
                      keyset: jwt.PyJWKSet) -> Dict[str, Any]:
    """Checks the signature (against the provider's published keys), issuer, audience, expiry and nonce."""
    header = jwt.get_unverified_header(id_token)
    if header.get("alg") not in ALLOWED_ALGORITHMS:
        raise SignInDenied("The ID token uses an algorithm that is not accepted")
    kid = header.get("kid")
    matching = [k for k in keyset.keys if kid is None or k.key_id == kid]
    if not matching:
        raise SignInDenied("The ID token is signed with an unknown key")
    try:
        claims = jwt.decode(id_token, matching[0].key, algorithms=ALLOWED_ALGORITHMS,
                            audience=provider["client_id"], issuer=discovery["issuer"], leeway=60,
                            options={"require": ["exp", "iat", "iss", "aud", "sub"]})
    except jwt.PyJWTError as e:
        raise SignInDenied(f"The ID token was not accepted: {type(e).__name__}")
    if claims.get("nonce") != expected_nonce:
        raise SignInDenied("The ID token nonce does not match this sign-in")
    return claims


async def oidc_claims_to_account(db: AsyncSession, provider: Dict[str, Any], claims: Dict[str, Any]) -> User:
    username = claim_value(claims, provider.get("username_claim") or "preferred_username")
    if not isinstance(username, str) or not username:
        raise SignInDenied(f"The ID token has no '{provider.get('username_claim')}' claim")
    email = claims.get("email") if isinstance(claims.get("email"), str) else None
    groups = as_list(claim_value(claims, provider.get("groups_claim") or ""))
    role = decide_roles(groups, provider.get("role_map") or {}, provider.get("default_role") or None,
                        email=email, allowed_emails=provider.get("allowed_emails"))
    return await sign_in_account(db, username=username, auth_type=AuthType.oauth,
                                 source=f"oidc:{provider['id']}", role=role, email=email)


def new_state_store_key(state: str) -> str:
    return f"jarn:sso:state:{state}"


# ---------- LDAP / Active Directory ----------

def ldap_authenticate(username: str, password: str, cfg: Dict[str, Any],
                      make_connection: Optional[Callable[..., Any]] = None) -> Optional[Dict[str, Any]]:
    """Checks a user name and password against the directory. Returns the entry's details, or None when the
    password is wrong or the user does not exist. The directory is queried with the service account first, and the
    password is then tried as the user's own bind, so the password never has to be stored."""
    if not username or not password:
        return None
    user = _ldap_find_user(username, cfg, make_connection)
    if user is None:
        return None
    bind_ok = _ldap_bind(user["dn"], password, cfg, make_connection)
    if not bind_ok:
        return None
    user["groups"] = _ldap_groups(user, cfg, make_connection)
    return user


def _ldap_server(cfg: Dict[str, Any]):
    from ldap3 import Server, Tls
    import ssl
    tls = None
    if cfg.get("use_ssl") or cfg.get("starttls"):
        tls = Tls(validate=ssl.CERT_REQUIRED if cfg.get("verify_tls", True) else ssl.CERT_NONE)
    return Server(cfg["host"], port=int(cfg.get("port") or (636 if cfg.get("use_ssl") else 389)),
                  use_ssl=bool(cfg.get("use_ssl")), tls=tls, connect_timeout=10)


class LdapBindFailed(Exception):
    pass


def _ldap_connection(cfg, user: Optional[str], password: Optional[str], make_connection):
    """An authenticated connection. ldap3 reports a refused bind by returning False rather than raising, so the
    result is checked here; a bind that did not succeed must never be treated as a sign-in."""
    from ldap3 import Connection
    if make_connection is not None:
        conn = make_connection(user=user, password=password)
    else:
        conn = Connection(_ldap_server(cfg), user=user, password=password, auto_bind=False, receive_timeout=15)
        if cfg.get("starttls") and not cfg.get("use_ssl"):
            conn.open()
            conn.start_tls()
        conn.bind()
    if not conn.bound:
        raise LdapBindFailed("The directory refused the bind")
    return conn


def _ldap_find_user(username: str, cfg: Dict[str, Any], make_connection) -> Optional[Dict[str, Any]]:
    from ldap3 import SUBTREE
    from ldap3.utils.conv import escape_filter_chars
    conn = _ldap_connection(cfg, cfg.get("bind_dn") or None, cfg.get("bind_password") or None, make_connection)
    try:
        search_filter = (cfg.get("user_filter") or "(&(objectClass=person)({attr}={username}))").replace(
            "{username}", escape_filter_chars(username)).replace("{attr}", cfg.get("username_attribute") or "uid")
        conn.search(cfg["base_dn"], search_filter, search_scope=SUBTREE,
                    attributes=[cfg.get("username_attribute") or "uid", cfg.get("email_attribute") or "mail",
                                cfg.get("group_attribute") or "memberOf"])
        if len(conn.entries) != 1:
            return None
        entry = conn.entries[0]
        attrs = entry.entry_attributes_as_dict
        name = _first(attrs.get(cfg.get("username_attribute") or "uid")) or username
        return {
            "dn": entry.entry_dn,
            "username": str(name),
            "email": _first(attrs.get(cfg.get("email_attribute") or "mail")),
            "member_of": [str(v) for v in (attrs.get(cfg.get("group_attribute") or "memberOf") or [])],
        }
    finally:
        conn.unbind()


def _ldap_bind(user_dn: str, password: str, cfg: Dict[str, Any], make_connection) -> bool:
    try:
        conn = _ldap_connection(cfg, user_dn, password, make_connection)
    except (LdapBindFailed, Exception):
        return False
    conn.unbind()
    return True


def _ldap_groups(user: Dict[str, Any], cfg: Dict[str, Any], make_connection) -> List[str]:
    """Groups from the user's memberOf attribute (Active Directory). Falls back to searching for groups that list the
    user as a member (OpenLDAP groupOfNames)."""
    groups = list(user.get("member_of") or [])
    if groups or not cfg.get("group_base_dn"):
        return groups
    conn = _ldap_connection(cfg, cfg.get("bind_dn") or None, cfg.get("bind_password") or None, make_connection)
    try:
        from ldap3 import SUBTREE
        from ldap3.utils.conv import escape_filter_chars
        conn.search(cfg["group_base_dn"], f"(member={escape_filter_chars(user['dn'])})",
                    search_scope=SUBTREE, attributes=["cn"])
        return [e.entry_dn for e in conn.entries]
    finally:
        conn.unbind()


def _first(value):
    if isinstance(value, list):
        return value[0] if value else None
    return value


def ldap_role(groups: List[str], cfg: Dict[str, Any]) -> Optional[str]:
    """Role for LDAP groups. Role map keys may be full group DNs or just the CN."""
    role_map = cfg.get("role_map") or {}
    normalised = {}
    for key, role in role_map.items():
        normalised[key.lower()] = role
        if key.lower().startswith("cn="):
            normalised[key.split(",")[0][3:].lower()] = role
    groups_l = [g.lower() for g in groups]
    cns = [g.split(",")[0][3:].lower() if g.lower().startswith("cn=") else g.lower() for g in groups]
    matched = {normalised[k] for k in groups_l + cns if k in normalised and normalised[k] in ROLE_RANK}
    mapped = highest_role(sorted(matched))
    if mapped:
        return mapped
    return cfg.get("default_role") if cfg.get("default_role") in ROLE_RANK else None


async def ldap_sign_in(db: AsyncSession, username: str, password: str, cfg: Dict[str, Any],
                       make_connection=None) -> User:
    """Authenticates against the directory and syncs the account. Raises SignInDenied when access is refused."""
    entry = await asyncio.to_thread(ldap_authenticate, username, password, cfg, make_connection)
    if entry is None:
        raise SignInDenied("Invalid username or password")
    role = ldap_role(entry["groups"], cfg)
    return await sign_in_account(db, username=entry["username"], auth_type=AuthType.ldap, source="ldap",
                                 role=role, email=entry.get("email"))


def ldap_test(username: str, cfg: Dict[str, Any], make_connection=None) -> Dict[str, Any]:
    """Administrator check: can the service account search, and what does a user resolve to? No password is used."""
    user = _ldap_find_user(username, cfg, make_connection)
    if user is None:
        return {"found": False, "detail": "No single matching entry was found for this user name"}
    groups = _ldap_groups(user, cfg, make_connection)
    return {"found": True, "dn": user["dn"], "groups": groups, "role": ldap_role(groups, cfg)}
