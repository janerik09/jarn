import base64
import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import bcrypt
import jwt
from jwt import PyJWTError as JWTError
from app.config import settings

def _prehash(password: str) -> str:
    # bcrypt only reads the first 72 bytes. A SHA-256 digest first means long passphrases are not cut short.
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password).encode("ascii"), bcrypt.gensalt()).decode("ascii")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return False
    try:
        return bcrypt.checkpw(_prehash(plain_password).encode("ascii"), hashed_password.encode("ascii"))
    except ValueError:
        return False


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )

    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.algorithm
    )
    return encoded_jwt


def create_refresh_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None
) -> str:
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            days=settings.refresh_token_expire_days
        )

    to_encode.update({"exp": expire, "type": "refresh", "jti": uuid.uuid4().hex})
    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.algorithm
    )
    return encoded_jwt


def decode_token(token: str) -> Optional[Dict[str, Any]]:
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm]
        )
        return payload
    except JWTError:
        return None


SEALED_MARKER = "__sealed"
_SEAL_VERSION = "v1"


def require_credential_key() -> None:
    """Fail fast when the encryption key is missing or malformed."""
    _credential_key()


DEFAULT_SECRET_KEY = "your-secret-key-change-in-production"


def require_secret_key() -> None:
    """In production the signing key must be a real random value; anyone can forge sessions with the default."""
    if settings.environment != "production":
        return
    if settings.secret_key == DEFAULT_SECRET_KEY or len(settings.secret_key) < 32:
        raise RuntimeError("SECRET_KEY must be set to a random value of at least 32 characters in production")


def _credential_key() -> bytes:
    raw = settings.credential_encryption_key
    if not raw:
        raise RuntimeError(
            "CREDENTIAL_ENCRYPTION_KEY is not set. Put it in the project .env file; "
            "refusing to store credentials unencrypted."
        )
    try:
        key = base64.urlsafe_b64decode(raw)
    except (ValueError, TypeError) as exc:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY is not valid base64") from exc
    if len(key) != 32:
        raise RuntimeError("CREDENTIAL_ENCRYPTION_KEY must decode to exactly 32 bytes")
    return key


def is_sealed(value: Any) -> bool:
    return isinstance(value, dict) and SEALED_MARKER in value


def seal(data: Dict[str, Any]) -> Dict[str, Any]:
    """Encrypt a JSON-compatible dict with AES-256-GCM. The result is what gets stored."""
    if is_sealed(data):
        return data
    nonce = os.urandom(12)
    ciphertext = AESGCM(_credential_key()).encrypt(nonce, json.dumps(data).encode(), None)
    token = base64.b64encode(nonce + ciphertext).decode()
    return {SEALED_MARKER: f"{_SEAL_VERSION}:{token}"}


def unseal(value: Dict[str, Any]) -> Dict[str, Any]:
    """Decrypt a stored value. Rows written before encryption existed are returned unchanged."""
    if not is_sealed(value):
        return value
    version, _, token = str(value[SEALED_MARKER]).partition(":")
    if version != _SEAL_VERSION:
        raise ValueError(f"Unsupported sealed value version: {version}")
    blob = base64.b64decode(token)
    plaintext = AESGCM(_credential_key()).decrypt(blob[:12], blob[12:], None)
    return json.loads(plaintext)
