"""
Time-based one-time passwords (RFC 6238) for authenticator apps: Google Authenticator, Microsoft Authenticator,
Authy, 1Password, Bitwarden and others. SHA-1, 6 digits, 30-second steps: the defaults those apps expect.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from typing import Optional
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
ISSUER = "JÁRN"


def new_secret() -> str:
    """Base32 secret of 160 bits, the length authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def code_at(secret: str, timestamp: float) -> str:
    counter = int(timestamp // STEP_SECONDS)
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % (10 ** DIGITS)).zfill(DIGITS)


def verify(secret: str, code: str, now: Optional[float] = None, window: int = 1) -> bool:
    """Accept the current code and one step either side, to allow for clock drift."""
    code = (code or "").replace(" ", "")
    if not code.isdigit() or len(code) != DIGITS:
        return False
    now = time.time() if now is None else now
    return any(hmac.compare_digest(code_at(secret, now + step * STEP_SECONDS), code)
               for step in range(-window, window + 1))


def provisioning_uri(secret: str, account: str) -> str:
    label = quote(f"{ISSUER}:{account}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")
