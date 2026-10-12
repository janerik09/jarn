from app.connectors.ssh import pinned_host_key
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from typing import Dict, List, Optional
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_
from app.database import get_db
from app.models import Server, PatchJob, PatchJobExecution
from app.models.server import OSType, AuthMethod, ConnectivityStatus
from app.models.job import ExecutionStatus
from app.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


class ServerCreate(BaseModel):
    hostname: str
    ip_address: Optional[str] = None
    os_type: str
    os_version: Optional[str] = None
    auth_method: str
    port: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None


class ServerUpdate(BaseModel):
    hostname: Optional[str] = None
    ip_address: Optional[str] = None
    os_version: Optional[str] = None
    port: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    private_key: Optional[str] = None


class ServerResponse(BaseModel):
    id: str
    hostname: str
    ip_address: Optional[str] = None
    os_type: str
    os_version: Optional[str] = None
    auth_method: str
    port: Optional[str] = None
    connectivity_status: str
    created_at: str
    pending_patch_count: Optional[int] = None
    last_patch_scan_at: Optional[str] = None
    pending_scan_error: Optional[str] = None
    health: Optional[dict] = None

    class Config:
        from_attributes = True

    @classmethod
    def model_validate(cls, obj):
        if isinstance(obj, dict):
            if 'ip_address' in obj and obj['ip_address'] is not None:
                obj['ip_address'] = str(obj['ip_address'])
        return super().model_validate(obj)


@router.post("", response_model=ServerResponse, status_code=status.HTTP_201_CREATED)
async def create_server(
    request: ServerCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new server with auto-detection of OS version if not provided"""
    try:
        # Validate OS type
        try:
            os_type_enum = OSType[request.os_type.lower()]
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid OS type: {request.os_type}")

        # Validate auth method
        try:
            auth_method_enum = AuthMethod[request.auth_method.lower()]
        except KeyError:
            raise HTTPException(status_code=400, detail=f"Invalid auth method: {request.auth_method}")

        # Check if server already exists
        result = await db.execute(
            select(Server).where(Server.hostname == request.hostname)
        )
        if result.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Server already exists")

        # Create credentials dict
        credentials = {}
        if request.username:
            credentials["username"] = request.username
        if request.password:
            credentials["password"] = request.password
        if request.private_key:
            credentials["private_key"] = request.private_key

        # Auto-detect OS version if not provided
        os_version = request.os_version
        if not os_version:
            logger.info("auto_detecting_os_version", hostname=request.hostname)
            try:
                from app.connectors.ssh import SSHConnector

                connect_host = str(request.ip_address) if request.ip_address else request.hostname
                connector = SSHConnector(
                    hostname=connect_host,
                    username=request.username,
                    private_key=request.private_key,
                    password=request.password,
                    port=int(request.port) if request.port else 22
                )

                if await connector.connect():
                    detection = await connector.detect_os_version()
                    await connector.disconnect()

                    if detection.get("os_version"):
                        os_version = detection["os_version"]
                        logger.info("os_version_auto_detected",
                                   hostname=request.hostname,
                                   version=os_version,
                                   method=detection.get("detection_method"))
                    else:
                        logger.warning("os_version_detection_failed", hostname=request.hostname)
            except Exception as e:
                logger.warning("os_version_auto_detection_error",
                              hostname=request.hostname, error=str(e))

        server = Server(
            hostname=request.hostname,
            ip_address=request.ip_address,
            os_type=os_type_enum,
            os_version=os_version,
            auth_method=auth_method_enum,
            port=request.port,
            credentials=credentials,
            connectivity_status=ConnectivityStatus.unknown
        )

        db.add(server)
        await db.commit()
        await db.refresh(server)

        logger.info("server_created", server_id=str(server.id), hostname=server.hostname,
                   os_version=server.os_version)

        from app.tasks import collect_inventory_task
        collect_inventory_task.delay(str(server.id))

        return ServerResponse(
            id=str(server.id),
            hostname=str(server.ip_address) if server.ip_address else server.hostname,
            ip_address=str(server.ip_address) if server.ip_address else None,
            os_type=server.os_type.value,
            os_version=server.os_version,
            auth_method=server.auth_method.value,
            port=server.port,
            connectivity_status=server.connectivity_status.value,
            created_at=server.created_at.isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("server_creation_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=List[ServerResponse])
async def list_servers(
    os_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """List all servers, optionally filtered by OS type"""
    try:
        query = select(Server)

        if os_type:
            try:
                os_type_enum = OSType[os_type.lower()]
                query = query.where(Server.os_type == os_type_enum)
            except KeyError:
                raise HTTPException(status_code=400, detail=f"Invalid OS type: {os_type}")

        result = await db.execute(query.order_by(Server.hostname))
        servers = result.scalars().all()

        return [
            ServerResponse(
                id=str(s.id),
                hostname=s.hostname,
                ip_address=str(s.ip_address) if s.ip_address else None,
                os_type=s.os_type.value,
                os_version=s.os_version,
                auth_method=s.auth_method.value,
                connectivity_status=s.connectivity_status.value,
                created_at=s.created_at.isoformat(),
                pending_patch_count=(s.metadata_ or {}).get("pending_patch_count"),
                pending_scan_error=(s.metadata_ or {}).get("scan_error"),
                health=(s.metadata_ or {}).get("health"),
                last_patch_scan_at=s.last_patch_scan_at.isoformat() if s.last_patch_scan_at else None,
            )
            for s in servers
        ]
    except HTTPException:
        raise
    except Exception as e:
        logger.error("server_list_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        return ServerResponse(
            id=str(server.id),
            hostname=str(server.ip_address) if server.ip_address else server.hostname,
            ip_address=str(server.ip_address) if server.ip_address else None,
            os_type=server.os_type.value,
            os_version=server.os_version,
            auth_method=server.auth_method.value,
            port=server.port,
            connectivity_status=server.connectivity_status.value,
            created_at=server.created_at.isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("server_get_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: str,
    request: ServerUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")
        from app.services import audit as _audit
        _before = _audit.copy_json({"hostname": server.hostname, "ip_address": str(server.ip_address) if server.ip_address else None,
                                    "os_version": server.os_version, "port": server.port, "credentials": dict(server.credentials or {})})

        # Update fields
        if request.hostname:
            server.hostname = request.hostname
        if request.ip_address:
            server.ip_address = request.ip_address
        if request.os_version:
            server.os_version = request.os_version
        if request.port:
            server.port = request.port

        # Update credentials
        if request.username or request.password or request.private_key:
            # Build a new dict. Mutating the loaded dict in place is not detected, so the change would not be saved.
            credentials = dict(server.credentials or {})
            if request.username:
                credentials["username"] = request.username
            if request.password:
                credentials["password"] = request.password
            if request.private_key:
                credentials["private_key"] = request.private_key
            server.credentials = credentials

        await db.commit()
        await db.refresh(server)
        _after = _audit.copy_json({"hostname": server.hostname, "ip_address": str(server.ip_address) if server.ip_address else None,
                                   "os_version": server.os_version, "port": server.port, "credentials": dict(server.credentials or {})})
        await _audit.record('config.update', category='server', resource_type='server', resource_id=str(server.id),
                            details={'changes': _audit.diff(_before, _after)})

        logger.info("server_updated", server_id=server_id, hostname=server.hostname)

        return ServerResponse(
            id=str(server.id),
            hostname=str(server.ip_address) if server.ip_address else server.hostname,
            ip_address=str(server.ip_address) if server.ip_address else None,
            os_type=server.os_type.value,
            os_version=server.os_version,
            auth_method=server.auth_method.value,
            port=server.port,
            connectivity_status=server.connectivity_status.value,
            created_at=server.created_at.isoformat()
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("server_update_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        await db.delete(server)
        await db.commit()

        logger.info("server_deleted", server_id=server_id, hostname=server.hostname)

        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error("server_delete_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{server_id}/test", status_code=status.HTTP_200_OK)
async def test_connectivity(server_id: str, db: AsyncSession = Depends(get_db)):
    """Test connection to a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        # Determine connector type and test connection
        from app.connectors import SSHConnector
        connector = SSHConnector(
            hostname=str(server.ip_address) if server.ip_address else server.hostname,
            username=server.credentials.get("username") if server.credentials else None,
            private_key=server.credentials.get("private_key") if server.credentials else None,
            password=server.credentials.get("password") if server.credentials else None,
            host_key=pinned_host_key(server.metadata_),
        )

        connected = await connector.connect()

        if connected:
            health = await connector.health_check()
            await connector.disconnect()

            # Update server connectivity status based on health check
            health_status = health.get("status", "unknown")
            if health_status == "healthy":
                server.connectivity_status = ConnectivityStatus.healthy
            elif health_status == "degraded":
                server.connectivity_status = ConnectivityStatus.degraded
            else:
                server.connectivity_status = ConnectivityStatus.unreachable

            server.last_check_at = datetime.utcnow()
            await db.commit()

            logger.info("connectivity_test_passed", server_id=server_id,
                       hostname=server.hostname, health_status=health_status)

            return {
                "server_id": str(server.id),
                "hostname": server.hostname,
                "status": "connected",
                "health": health_status
            }
        else:
            server.connectivity_status = ConnectivityStatus.unreachable
            server.last_check_at = datetime.utcnow()
            await db.commit()

            logger.info("connectivity_test_failed", server_id=server_id,
                       hostname=server.hostname)

            return {
                "server_id": str(server.id),
                "hostname": server.hostname,
                "status": "unreachable",
                "message": "Connection failed"
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("connectivity_test_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{server_id}/patches")
async def get_server_patches(
    server_id: str,
    include_deployed: bool = False,
    refresh: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """Patches pending on a server. Uses the last scan's stored list unless refresh=true."""
    from app.services.scan_snapshot import build_snapshot

    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    metadata = dict(server.metadata_ or {})
    snapshot = metadata.get("scan_snapshot")
    live = refresh or not snapshot

    if live:
        if not server.credentials or (not server.credentials.get("username") and not server.credentials.get("private_key")):
            return {"server_id": str(server.id), "hostname": server.hostname, "patches": [],
                    "message": "No credentials configured"}
        from app.connectors import SSHConnector
        connector = SSHConnector(
            hostname=str(server.ip_address) if server.ip_address else server.hostname,
            username=server.credentials.get("username"),
            private_key=server.credentials.get("private_key"),
            password=server.credentials.get("password"),
            port=int(server.port) if server.port else 22,
            host_key=pinned_host_key(server.metadata_),
        )
        if not await connector.connect():
            return {"server_id": str(server.id), "hostname": server.hostname, "patches": [],
                    "message": "Failed to connect to server"}
        patches = await connector.scan_available_patches()
        scan_info = connector.scan_info or {}
        await connector.disconnect()
        snapshot = build_snapshot(patches, scan_info)
        metadata["scan_snapshot"] = snapshot
        metadata["pending_patch_count"] = snapshot["upgradable_packages"]
        metadata["scan_error"] = None
        server.metadata_ = metadata
        server.last_patch_scan_at = datetime.utcnow()
        await db.commit()

    exec_result = await db.execute(
        select(PatchJobExecution).where(
            and_(PatchJobExecution.server_id == server.id, PatchJobExecution.status == ExecutionStatus.success)
        )
    )
    deployed_patch_ids = set()
    for execution in exec_result.scalars().all():
        job_result = await db.execute(select(PatchJob).where(PatchJob.id == execution.job_id))
        job = job_result.scalar_one_or_none()
        # A dry run installs nothing, so it does not count as deployed
        if job and job.patch_ids and not job.dry_run:
            deployed_patch_ids.update(job.patch_ids)

    all_patches = snapshot["patches"]
    shown = all_patches if include_deployed else [p for p in all_patches if p["name"] not in deployed_patch_ids]

    return {
        "server_id": str(server.id),
        "hostname": server.hostname,
        "patches": [{**p, "already_deployed": p["name"] in deployed_patch_ids} for p in shown],
        "total_available": snapshot["total_available"],
        "already_deployed": len(deployed_patch_ids),
        "new_patches": len([p for p in all_patches if p["name"] not in deployed_patch_ids]),
        "upgradable_packages": snapshot["upgradable_packages"],
        "security_packages": snapshot["security_packages"],
        "index_refreshed": snapshot["index_refreshed"],
        "collected_at": snapshot["collected_at"],
        "from_cache": not live,
        "include_deployed": include_deployed,
    }

@router.post("/{server_id}/test-connection")
async def test_server_connection(
    server_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Test SSH connection to a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        from app.connectors.ssh import SSHConnector

        # Use IP address if available, fall back to hostname
        # This allows connection even if DNS resolution fails
        connect_host = str(server.ip_address) if server.ip_address else server.hostname

        connector = SSHConnector(
            hostname=connect_host,
            port=int(server.port or 22),
            username=server.credentials.get("username") if server.credentials else None,
            auth_method=server.auth_method.value,
            password=server.credentials.get("password") if server.credentials else None,
            private_key_path=server.credentials.get("private_key_path") if server.credentials else None,
            host_key=pinned_host_key(server.metadata_),
        )

        connection_result = await connector.test_connection()

        if connection_result:
            server.connectivity_status = ConnectivityStatus.healthy
            await db.commit()
            return {"status": "success", "message": "Connection successful", "connectivity": "healthy"}
        else:
            server.connectivity_status = ConnectivityStatus.unreachable
            await db.commit()
            return {"status": "failed", "message": "Connection failed", "connectivity": "unreachable"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error("connection_test_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Connection test failed: {str(e)}")


@router.post("/scan-all", status_code=202)
async def scan_all_servers():
    from app.tasks import rescan_stale_servers

    task = rescan_stale_servers.delay(True)
    logger.info("scan_all_queued", task_id=task.id)
    return {"task_id": task.id, "status": "queued"}


class DiscoverRequest(BaseModel):
    targets: str
    port: int = 22
    username: str
    auth_method: str = "ssh_password"
    password: Optional[str] = None
    private_key: Optional[str] = None
    # address -> SHA256 fingerprint an administrator approved after reading it from the server
    approved_fingerprints: Dict[str, str] = {}


@router.post("/discover", status_code=202)
async def start_discovery(request: DiscoverRequest):
    """Queue a subnet/range scan. Only hosts verified as supported Linux are added."""
    from app.services.bulk_discovery import parse_targets
    from app.tasks import discover_servers

    try:
        addresses = parse_targets(request.targets)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if request.auth_method == "ssh_key" and not request.private_key:
        raise HTTPException(status_code=400, detail="A private key is required for key authentication")
    if request.auth_method != "ssh_key" and not request.password:
        raise HTTPException(status_code=400, detail="A password is required for password authentication")

    task = discover_servers.delay(
        request.targets, request.port, request.username, request.auth_method,
        request.password, request.private_key, request.approved_fingerprints,
    )
    logger.info("bulk_discovery_queued", task_id=task.id, addresses=len(addresses))
    return {"task_id": task.id, "total_addresses": len(addresses), "status": "queued"}


@router.get("/discover/{task_id}")
async def get_discovery_status(task_id: str):
    from celery.result import AsyncResult
    from app.celery_app import app as celery_app

    result = AsyncResult(task_id, app=celery_app)
    if result.state == "SUCCESS":
        return {"task_id": task_id, "state": result.state, "result": result.result}
    if result.state == "FAILURE":
        return {"task_id": task_id, "state": result.state, "error": str(result.result)}
    return {"task_id": task_id, "state": result.state}


@router.get("/{server_id}/reboot-status")
async def check_reboot_status(
    server_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Check if a reboot is pending on a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        if not server.credentials or (not server.credentials.get("username") and not server.credentials.get("private_key")):
            return {
                "server_id": str(server.id),
                "hostname": server.hostname,
                "reboot_required": False,
                "message": "No credentials configured"
            }

        from app.connectors.ssh import SSHConnector

        connect_host = str(server.ip_address) if server.ip_address else server.hostname
        connector = SSHConnector(
            hostname=connect_host,
            username=server.credentials.get("username"),
            private_key=server.credentials.get("private_key"),
            password=server.credentials.get("password"),
            port=int(server.port) if server.port else 22,
            host_key=pinned_host_key(server.metadata_),
        )

        if not await connector.connect():
            return {
                "server_id": str(server.id),
                "hostname": server.hostname,
                "reboot_required": False,
                "message": "Failed to connect to server"
            }

        reboot_status = await connector.check_reboot_required()
        await connector.disconnect()

        logger.info("reboot_status_checked", server_id=str(server.id),
                   reboot_required=reboot_status.get("reboot_required"))

        return {
            "server_id": str(server.id),
            "hostname": server.hostname,
            "reboot_required": reboot_status.get("reboot_required", False),
            "reboot_reason": reboot_status.get("reboot_reason"),
            "reboot_pending_file": reboot_status.get("reboot_pending_file")
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("reboot_check_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Reboot check failed: {str(e)}")


@router.post("/{server_id}/reboot")
async def reboot_server(
    server_id: str,
    graceful: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """Schedule a reboot on a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        if not server.credentials or (not server.credentials.get("username") and not server.credentials.get("private_key")):
            raise HTTPException(status_code=400, detail="No credentials configured")

        from app.connectors.ssh import SSHConnector

        connect_host = str(server.ip_address) if server.ip_address else server.hostname
        connector = SSHConnector(
            hostname=connect_host,
            username=server.credentials.get("username"),
            private_key=server.credentials.get("private_key"),
            password=server.credentials.get("password"),
            port=int(server.port) if server.port else 22,
            host_key=pinned_host_key(server.metadata_),
        )

        if not await connector.connect():
            raise HTTPException(status_code=500, detail="Failed to connect to server")

        reboot_result = await connector.reboot_server(graceful=graceful)
        await connector.disconnect()

        if reboot_result.get("success"):
            logger.info("server_reboot_initiated", server_id=str(server.id),
                       graceful=graceful)
            return reboot_result
        else:
            raise HTTPException(status_code=500, detail=reboot_result.get("error", "Failed to reboot"))

    except HTTPException:
        raise
    except Exception as e:
        logger.error("reboot_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Reboot failed: {str(e)}")


@router.post("/{server_id}/detect-version")
async def detect_os_version(
    server_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Detect and update OS version for a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        if not server.credentials or (not server.credentials.get("username") and not server.credentials.get("private_key")):
            raise HTTPException(status_code=400, detail="No credentials configured")

        from app.connectors.ssh import SSHConnector

        connect_host = str(server.ip_address) if server.ip_address else server.hostname
        connector = SSHConnector(
            hostname=connect_host,
            username=server.credentials.get("username"),
            private_key=server.credentials.get("private_key"),
            password=server.credentials.get("password"),
            port=int(server.port) if server.port else 22,
            host_key=pinned_host_key(server.metadata_),
        )

        if not await connector.connect():
            raise HTTPException(status_code=500, detail="Failed to connect to server")

        detection = await connector.detect_os_version()
        await connector.disconnect()

        if detection.get("os_version"):
            # Update server with detected version
            server.os_version = detection["os_version"]
            await db.commit()

            logger.info("server_version_updated", server_id=str(server.id),
                       old_version=server.os_version,
                       new_version=detection["os_version"],
                       detection_method=detection.get("detection_method"))

            return {
                "server_id": str(server.id),
                "hostname": server.hostname,
                "os_version": server.os_version,
                "distro_name": detection.get("distro_name"),
                "full_version": detection.get("full_version"),
                "detection_method": detection.get("detection_method"),
                "message": f"OS version detected: {detection['os_version']}"
            }
        else:
            logger.warning("os_version_detection_failed", server_id=str(server.id))
            return {
                "server_id": str(server.id),
                "hostname": server.hostname,
                "message": "Could not detect OS version",
                "detection_info": detection
            }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("version_detection_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Version detection failed: {str(e)}")


@router.get("/{server_id}/critical-applications")
async def detect_critical_applications(
    server_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Detect critical applications running on a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        if not server.credentials or (not server.credentials.get("username") and not server.credentials.get("private_key")):
            return {
                "server_id": str(server.id),
                "hostname": server.hostname,
                "applications": [],
                "critical_services": [],
                "message": "No credentials configured"
            }

        from app.connectors.ssh import SSHConnector

        connect_host = str(server.ip_address) if server.ip_address else server.hostname
        connector = SSHConnector(
            hostname=connect_host,
            username=server.credentials.get("username"),
            private_key=server.credentials.get("private_key"),
            password=server.credentials.get("password"),
            port=int(server.port) if server.port else 22,
            host_key=pinned_host_key(server.metadata_),
        )

        if not await connector.connect():
            return {
                "server_id": str(server.id),
                "hostname": server.hostname,
                "applications": [],
                "critical_services": [],
                "message": "Failed to connect to server"
            }

        detection = await connector.detect_critical_applications()
        await connector.disconnect()

        logger.info("critical_apps_detected", server_id=str(server.id),
                   applications=detection.get("applications", []))

        return {
            "server_id": str(server.id),
            "hostname": server.hostname,
            "applications": detection.get("applications", []),
            "critical_services": detection.get("critical_services", []),
            "docker_running": detection.get("docker_running", False),
            "kubernetes_running": detection.get("kubernetes_running", False)
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("app_detection_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Application detection failed: {str(e)}")


class ServerMetaUpdate(BaseModel):
    tags: List[str] = []
    notes: str = ""


def _iso(value):
    return value.isoformat() if value else None


@router.get("/{server_id}/detail")
async def get_server_detail(server_id: str, db: AsyncSession = Depends(get_db)):
    server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    meta = dict(server.metadata_ or {})
    rows = (await db.execute(
        select(PatchJobExecution, PatchJob.name)
        .join(PatchJob, PatchJob.id == PatchJobExecution.job_id)
        .where(PatchJobExecution.server_id == server.id)
        .order_by(PatchJobExecution.created_at.desc())
        .limit(10)
    )).all()
    return {
        "id": str(server.id),
        "hostname": server.hostname,
        "ip_address": str(server.ip_address) if server.ip_address else None,
        "os_type": server.os_type.value,
        "os_version": server.os_version,
        "auth_method": server.auth_method.value,
        "port": server.port,
        "connectivity_status": server.connectivity_status.value,
        "last_check_at": _iso(server.last_check_at),
        "last_patch_scan_at": _iso(server.last_patch_scan_at),
        "tags": meta.get("tags", []),
        "notes": meta.get("notes", ""),
        "inventory": meta.get("inventory"),
        "health": meta.get("health"),
        "pending_patch_count": meta.get("pending_patch_count"),
        "scan_error": meta.get("scan_error"),
        "backup_mapping": meta.get("backup"),
        "last_backup": meta.get("last_backup"),
        "recent_executions": [
            {
                "job_name": job_name,
                "status": e.status.value,
                "started": _iso(e.execution_start),
                "ended": _iso(e.execution_end),
                "error": e.error_message,
            }
            for e, job_name in rows
        ],
    }


@router.put("/{server_id}/tags-notes")
async def update_tags_and_notes(server_id: str, request: ServerMetaUpdate, db: AsyncSession = Depends(get_db)):
    server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    tags: List[str] = []
    for raw in request.tags:
        tag = raw.strip().lower()[:30]
        if tag and tag not in tags:
            tags.append(tag)
    if len(tags) > 20:
        raise HTTPException(status_code=400, detail="A server can have at most 20 tags")
    if len(request.notes) > 5000:
        raise HTTPException(status_code=400, detail="Notes can be at most 5000 characters")
    server.metadata_ = {**(server.metadata_ or {}), "tags": tags, "notes": request.notes}
    await db.commit()
    return {"tags": tags, "notes": request.notes}


@router.post("/{server_id}/inventory", status_code=202)
async def refresh_server_inventory(server_id: str, db: AsyncSession = Depends(get_db)):
    from app.tasks import collect_inventory_task

    server = (await db.execute(select(Server).where(Server.id == server_id))).scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")
    task = collect_inventory_task.delay(str(server.id))
    return {"task_id": task.id, "status": "queued"}
