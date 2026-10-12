"""
TLS for the web dashboard.

The settings (mode, redirect flag, self-signed request, uploaded certificate and private key) live in
SystemSettings.tls_config, which is encrypted at rest. This module turns those settings into the files the
web container reads from the shared /tls directory:

    server.crt   certificate chain (PEM)
    server.key   private key (PEM, owner read only)
    mode.env     TLS_MODE=http|self_signed|custom and REDIRECT_HTTPS=0|1

The web container watches mode.env and the certificate files, validates the nginx config, and reloads.
Its result is written back to applied.json, which the admin page shows.
"""

import datetime
import ipaddress
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from app.logging_config import get_logger

logger = get_logger(__name__)

TLS_DIR = os.environ.get("TLS_DIR", "/tls")
SELF_SIGNED_DAYS = 397  # Browsers reject longer-lived TLS certificates
MODES = ("http", "self_signed", "custom")
CERT_BLOCK = re.compile(rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", re.S)


def default_settings() -> Dict[str, Any]:
    return {"mode": "http", "redirect_https": False, "hostnames": [], "ips": []}


def _leaf_certificate(cert_pem: bytes) -> x509.Certificate:
    blocks = CERT_BLOCK.findall(cert_pem)
    if not blocks:
        raise ValueError("No certificate found. Upload a PEM file that starts with -----BEGIN CERTIFICATE-----")
    return x509.load_pem_x509_certificate(blocks[0])


def _public_key_der(public_key) -> bytes:
    return public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )


def generate_self_signed(hostnames: List[str], ips: List[str]) -> Tuple[str, str]:
    """Return (certificate_pem, private_key_pem) for an ECDSA P-256 self-signed server certificate."""
    if not hostnames and not ips:
        raise ValueError("Enter at least one hostname or IP address for the certificate")
    key = ec.generate_private_key(ec.SECP256R1())
    common_name = hostnames[0] if hostnames else ips[0]
    names: List[x509.GeneralName] = [x509.DNSName(h) for h in hostnames]
    for ip in ips:
        names.append(x509.IPAddress(ipaddress.ip_address(ip)))
    subject = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "JÁRN"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=SELF_SIGNED_DAYS))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(
            digital_signature=True, content_commitment=False, key_encipherment=False,
            data_encipherment=False, key_agreement=True, key_cert_sign=False, crl_sign=False,
            encipher_only=False, decipher_only=False,
        ), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return cert_pem, key_pem


def validate_custom(cert_pem: bytes, key_pem: bytes) -> Dict[str, Any]:
    """Check an uploaded certificate and key belong together and are usable. Returns a summary."""
    try:
        leaf = _leaf_certificate(cert_pem)
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(f"Certificate could not be read: {e}")
    try:
        key = serialization.load_pem_private_key(key_pem, password=None)
    except TypeError:
        raise ValueError("The private key is encrypted. Upload an unencrypted PEM key")
    except Exception as e:
        raise ValueError(f"Private key could not be read: {e}")
    if _public_key_der(leaf.public_key()) != _public_key_der(key.public_key()):
        raise ValueError("The private key does not match the certificate")
    not_after = leaf.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    if not_after <= datetime.datetime.now(datetime.timezone.utc):
        raise ValueError(f"The certificate expired on {not_after:%Y-%m-%d}")
    return summarize(cert_pem.decode(errors="replace"))


def summarize(cert_pem: str) -> Dict[str, Any]:
    leaf = _leaf_certificate(cert_pem.encode())
    sans: List[str] = []
    try:
        ext = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        sans = [str(n.value) for n in ext.value]
    except x509.ExtensionNotFound:
        pass
    not_after = leaf.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    days_left = (not_after - datetime.datetime.now(datetime.timezone.utc)).days
    return {
        "subject": leaf.subject.rfc4514_string(),
        "issuer": leaf.issuer.rfc4514_string(),
        "self_signed": leaf.subject == leaf.issuer,
        "sans": sans,
        "not_after": not_after.isoformat(),
        "days_left": days_left,
    }


def _atomic_write(path: str, data: str, mode: int) -> None:
    tmp = f"{path}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w") as f:
        f.write(data)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def active_material(tls: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The certificate and key for the selected mode. Both certificates are kept, so switching modes is safe."""
    mode = tls.get("mode", "http")
    if mode in ("self_signed", "custom"):
        return tls.get(mode) or None
    return None


def write_active_files(tls: Dict[str, Any]) -> None:
    """Write the files the web container reads. Called after every change and at backend startup."""
    os.makedirs(TLS_DIR, exist_ok=True)
    mode = tls.get("mode", "http")
    if mode not in MODES:
        raise ValueError(f"Unknown TLS mode: {mode}")
    material = active_material(tls)
    if material:
        _atomic_write(os.path.join(TLS_DIR, "server.crt"), material["cert_pem"], 0o644)
        _atomic_write(os.path.join(TLS_DIR, "server.key"), material["key_pem"], 0o600)
    else:
        # Plain HTTP needs no key on disk; the copy in the database is kept for switching back
        for name in ("server.crt", "server.key"):
            try:
                os.remove(os.path.join(TLS_DIR, name))
            except FileNotFoundError:
                pass
    _atomic_write(
        os.path.join(TLS_DIR, "mode.env"),
        f"TLS_MODE={mode}\nREDIRECT_HTTPS={1 if tls.get('redirect_https') else 0}\n",
        0o644,
    )


def read_applied_status() -> Optional[Dict[str, Any]]:
    try:
        with open(os.path.join(TLS_DIR, "applied.json")) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


async def apply_from_database() -> None:
    """Write the active TLS files from the stored settings. Runs at backend startup."""
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.settings import SystemSettings

    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
        tls = dict(row.tls_config or {}) if row and row.tls_config else default_settings()
    tls.setdefault("mode", "http")
    try:
        write_active_files(tls)
        logger.info("tls_files_written", mode=tls["mode"])
    except OSError as e:
        logger.error("tls_files_failed", error=str(e))


async def read_settings_for_metrics(db) -> Optional[Dict[str, Any]]:
    from sqlalchemy import select
    from app.models.settings import SystemSettings

    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    return dict(row.tls_config or {}) if row else None
