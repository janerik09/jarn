"""
SSH host keys of managed servers. A server is only connected to with the key an administrator approved, so an
intercepted connection cannot receive credentials. Fetching a key sends nothing but the TCP handshake. Approval
requires the fingerprint the administrator read from the server, and is refused if the server now presents another key.
"""

import asyncio
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.ssh import HOST_KEY_FIELD, fetch_host_key, host_key_fingerprint, pinned_host_key
from app.database import get_db
from app.logging_config import get_logger
from app.models import Server
from app.services import audit

logger = get_logger(__name__)
router = APIRouter()
FETCH_CONCURRENCY = 8


class ApproveRequest(BaseModel):
    fingerprint: str  # the SHA256 fingerprint the administrator compared with the server


def _address(server: Server) -> str:
    return str(server.ip_address) if server.ip_address else server.hostname


async def _probe(server: Server) -> Dict[str, Any]:
    """Reads the server's current key and compares it with the approved one. Never logs in."""
    pinned = pinned_host_key(server.metadata_)
    result: Dict[str, Any] = {
        "server_id": str(server.id), "hostname": server.hostname, "address": _address(server),
        "pinned_fingerprint": host_key_fingerprint(pinned) if pinned else None,
    }
    try:
        current = await asyncio.to_thread(fetch_host_key, _address(server), int(server.port or 22))
    except OSError as e:
        result.update(status="unreachable", detail=f"Could not read the host key: {e}", host_key_fingerprint=None)
        return result
    fingerprint = host_key_fingerprint(current)
    result["host_key_fingerprint"] = fingerprint
    if not pinned:
        result.update(status="unapproved", detail="No host key is approved for this server yet.")
    elif pinned == current:
        result.update(status="approved", detail="Matches the approved key.")
    else:
        result.update(status="changed", detail="The server presents a different key from the approved one. "
                                               "Confirm the change with the server's owner before approving it.")
    return result


async def _server(db: AsyncSession, server_id: str) -> Server:
    server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found")
    return server


@router.get("")
async def list_host_keys(db: AsyncSession = Depends(get_db)):
    """Every server with its approved fingerprint, without contacting any of them."""
    rows = (await db.execute(select(Server).order_by(Server.hostname))).scalars().all()
    return [{
        "server_id": str(s.id), "hostname": s.hostname, "address": _address(s),
        "pinned_fingerprint": host_key_fingerprint(pinned_host_key(s.metadata_)) if pinned_host_key(s.metadata_) else None,
    } for s in rows]


@router.post("/fetch")
async def fetch_all(db: AsyncSession = Depends(get_db)):
    """Reads the current key of every server, so the administrator can compare and approve them."""
    servers = (await db.execute(select(Server).order_by(Server.hostname))).scalars().all()
    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def one(server: Server) -> Dict[str, Any]:
        async with semaphore:
            return await _probe(server)

    return await asyncio.gather(*(one(s) for s in servers))


@router.post("/{server_id}/fetch")
async def fetch_one(server_id: str, db: AsyncSession = Depends(get_db)):
    return await _probe(await _server(db, server_id))


@router.post("/{server_id}/approve")
async def approve(server_id: str, request: ApproveRequest, db: AsyncSession = Depends(get_db)):
    server = await _server(db, server_id)
    # One read: the key stored is the one whose fingerprint was compared
    try:
        current = await asyncio.to_thread(fetch_host_key, _address(server), int(server.port or 22))
    except OSError as e:
        raise HTTPException(status_code=502, detail=f"Could not read the host key: {e}")
    fingerprint = host_key_fingerprint(current)
    if request.fingerprint.strip() != fingerprint:
        raise HTTPException(
            status_code=409,
            detail=f"Nothing was approved. The server now presents {fingerprint}, not {request.fingerprint.strip()}. Fetch it again and compare.",
        )
    previous = pinned_host_key(server.metadata_)
    server.metadata_ = {**dict(server.metadata_ or {}), HOST_KEY_FIELD: current}
    await db.commit()
    await audit.record("ssh.hostkey.approve", category="ssh", resource_type="server", resource_id=str(server.id),
                       details={"hostname": server.hostname, "fingerprint": fingerprint,
                                "replaced": host_key_fingerprint(previous) if previous else None})
    logger.info("ssh_host_key_approved", hostname=server.hostname)
    return {"server_id": str(server.id), "status": "approved", "fingerprint": fingerprint}


@router.delete("/{server_id}")
async def clear(server_id: str, db: AsyncSession = Depends(get_db)):
    """Removes the approved key. The server cannot be connected to until a key is approved again."""
    server = await _server(db, server_id)
    metadata = dict(server.metadata_ or {})
    previous = metadata.pop(HOST_KEY_FIELD, None)
    server.metadata_ = metadata
    await db.commit()
    await audit.record("ssh.hostkey.clear", category="ssh", resource_type="server", resource_id=str(server.id),
                       details={"hostname": server.hostname, "had_key": bool(previous)})
    return {"server_id": str(server.id), "status": "cleared"}
