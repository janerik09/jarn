import ipaddress
from typing import List

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.logging_config import get_logger
from app.models.settings import SystemSettings
from app.services import tls

logger = get_logger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 256 * 1024


class TlsUpdate(BaseModel):
    mode: str
    redirect_https: bool = False
    hostnames: List[str] = []
    ips: List[str] = []

    @field_validator("mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        if v not in tls.MODES:
            raise ValueError(f"mode must be one of {', '.join(tls.MODES)}")
        return v

    @field_validator("hostnames")
    @classmethod
    def _hostnames(cls, v: List[str]) -> List[str]:
        cleaned = [h.strip().lower() for h in v if h.strip()]
        for h in cleaned:
            if len(h) > 253 or any(c in h for c in " /\\'\"<>"):
                raise ValueError(f"Invalid hostname: {h}")
        return cleaned

    @field_validator("ips")
    @classmethod
    def _ips(cls, v: List[str]) -> List[str]:
        cleaned = []
        for ip in (i.strip() for i in v if i.strip()):
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise ValueError(f"Invalid IP address: {ip}")
            cleaned.append(ip)
        return cleaned


async def _settings_row(db: AsyncSession) -> SystemSettings:
    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    if row is None:
        row = SystemSettings()
        db.add(row)
        await db.flush()
    return row


def _status(tls_state: dict, request: Request) -> dict:
    host = request.url.hostname or "localhost"
    mode = tls_state.get("mode", "http")
    material = tls.active_material(tls_state)
    return {
        "mode": mode,
        "redirect_https": bool(tls_state.get("redirect_https")),
        "hostnames": tls_state.get("hostnames", []),
        "ips": tls_state.get("ips", []),
        "self_signed_available": bool(tls_state.get("self_signed")),
        "custom_available": bool(tls_state.get("custom")),
        "certificate": tls.summarize(material["cert_pem"]) if material else None,
        "applied": tls.read_applied_status(),
        "urls": {"http": f"http://{host}/", "https": f"https://{host}/"},
    }


@router.get("")
async def get_tls(request: Request, db: AsyncSession = Depends(get_db)):
    row = await _settings_row(db)
    return _status(dict(row.tls_config or {}) or tls.default_settings(), request)


@router.put("")
async def update_tls(payload: TlsUpdate, request: Request, db: AsyncSession = Depends(get_db)):
    from app.services import audit as _audit
    if payload.redirect_https and payload.mode == "http":
        raise HTTPException(status_code=400, detail="Redirecting to HTTPS needs an HTTPS mode")
    row = await _settings_row(db)
    _before = _audit.copy_json(dict(row.tls_config or {}))
    new = {**tls.default_settings(), **dict(row.tls_config or {})}
    new.update({
        "mode": payload.mode,
        "redirect_https": payload.redirect_https,
        "hostnames": payload.hostnames,
        "ips": payload.ips,
    })

    if payload.mode == "self_signed":
        previous = new.get("self_signed") or {}
        names_changed = (new.get("self_signed_names") != {"hostnames": payload.hostnames, "ips": payload.ips})
        if names_changed or not previous.get("cert_pem"):
            try:
                cert_pem, key_pem = tls.generate_self_signed(payload.hostnames, payload.ips)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e))
            new["self_signed"] = {"cert_pem": cert_pem, "key_pem": key_pem}
            new["self_signed_names"] = {"hostnames": payload.hostnames, "ips": payload.ips}
            logger.info("tls_self_signed_generated", hostnames=payload.hostnames, ips=payload.ips)
    elif payload.mode == "custom":
        if not new.get("custom"):
            raise HTTPException(status_code=400, detail="Upload a certificate and key before choosing a custom certificate")

    row.tls_config = new
    await db.commit()
    tls.write_active_files(new)
    await _audit.record('config.update', category='config', resource_type='tls',
                        details={'changes': _audit.diff(_before, _audit.copy_json(new))})
    return _status(new, request)


@router.post("/certificate")
async def upload_certificate(
    request: Request,
    certificate: UploadFile = File(...),
    private_key: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Upload a real certificate (PEM, leaf first, with any intermediates) and its unencrypted private key. Applies it."""
    cert_bytes = await certificate.read(MAX_UPLOAD_BYTES + 1)
    key_bytes = await private_key.read(MAX_UPLOAD_BYTES + 1)
    if len(cert_bytes) > MAX_UPLOAD_BYTES or len(key_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=400, detail="Files are too large for a certificate")
    try:
        tls.validate_custom(cert_bytes, key_bytes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    row = await _settings_row(db)
    new = {**tls.default_settings(), **dict(row.tls_config or {})}
    new["mode"] = "custom"
    new["custom"] = {"cert_pem": cert_bytes.decode(), "key_pem": key_bytes.decode()}
    row.tls_config = new
    await db.commit()
    tls.write_active_files(new)
    logger.info("tls_custom_certificate_uploaded")
    return _status(new, request)


@router.get("/certificate")
async def download_certificate(db: AsyncSession = Depends(get_db)):
    """The active certificate (public part only), for importing into browsers or clients that must trust it."""
    row = await _settings_row(db)
    material = tls.active_material(dict(row.tls_config or {}))
    if not material:
        raise HTTPException(status_code=404, detail="No certificate is active")
    from app.services import audit as _audit
    await _audit.record('certificate.download', category='system', resource_type='certificate', details={'mode': row.tls_config.get('mode') if row.tls_config else None})
    return Response(
        content=material["cert_pem"],
        media_type="application/x-pem-file",
        headers={"Content-Disposition": 'attachment; filename="jarn-server.crt"'},
    )
