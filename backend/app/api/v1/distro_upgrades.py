from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models import Server, PatchJob, PatchJobExecution, JobStatus, ExecutionStatus
from app.connectors.ssh import SSHConnector
from app.connectors.ssh import pinned_host_key
from app.services.distro_upgrade import DistroUpgradeChecker
from app.tasks import execute_distro_upgrade
from app.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()
candidates_router = APIRouter()


class UpgradeAnalysisRequest(BaseModel):
    target_version: str


class StartUpgradeRequest(BaseModel):
    target_version: str
    confirm: bool = False
    acknowledge_risks: bool = False


@router.get("/{server_id}/upgrade-options")
async def get_upgrade_options(
    server_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get available distro upgrades for a server"""
    try:
        result = await db.execute(
            select(Server).where(Server.id == server_id)
        )
        server = result.scalar_one_or_none()

        if not server:
            raise HTTPException(status_code=404, detail="Server not found")

        current_distro = DistroUpgradeChecker.get_current_distro(
            server.os_type.value, server.os_version
        )
        available_upgrades = DistroUpgradeChecker.get_available_upgrades(current_distro)

        logger.info("upgrade_options_retrieved", server_id=str(server.id),
                   current_distro=current_distro, available=available_upgrades)

        return {
            "server_id": str(server.id),
            "hostname": server.hostname,
            "current_distro": current_distro,
            "current_version": server.os_version,
            "available_upgrades": available_upgrades,
            "pre_upgrade_steps": DistroUpgradeChecker.get_pre_upgrade_steps(),
            "post_upgrade_steps": DistroUpgradeChecker.get_post_upgrade_steps(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("upgrade_options_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to get upgrade options: {str(e)}")


@router.post("/{server_id}/analyze-upgrade")
async def analyze_upgrade(
    server_id: str,
    request: UpgradeAnalysisRequest,
    db: AsyncSession = Depends(get_db)
):
    """Analyze if server is ready for distro upgrade"""
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

        # Get running critical applications
        detection = await connector.detect_critical_applications()

        analysis = await DistroUpgradeChecker.analyze_upgrade_readiness(
            connector,
            server.os_type.value,
            server.os_version,
            request.target_version,
            detection.get("applications", [])
        )
        await connector.disconnect()

        # Add rollback plan
        analysis["rollback_plan"] = DistroUpgradeChecker.get_rollback_plan(
            server.os_type.value,
            server.os_version
        )

        logger.info("upgrade_analysis_completed", server_id=str(server.id),
                   target_version=request.target_version,
                   status=analysis["upgrade_status"])

        return {
            "server_id": str(server.id),
            "hostname": server.hostname,
            "analysis": analysis,
            "running_services": detection.get("applications", []),
            "critical_services": detection.get("critical_services", []),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("upgrade_analysis_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Upgrade analysis failed: {str(e)}")


@router.post("/{server_id}/start-upgrade", status_code=202)
async def start_upgrade(
    server_id: str,
    request: StartUpgradeRequest,
    db: AsyncSession = Depends(get_db)
):
    """Queue a distro upgrade. Runs only when explicitly confirmed; the worker re-checks readiness first."""
    if not request.confirm:
        raise HTTPException(status_code=400, detail="Explicit confirmation is required to start an upgrade")

    result = await db.execute(select(Server).where(Server.id == server_id))
    server = result.scalar_one_or_none()
    if not server:
        raise HTTPException(status_code=404, detail="Server not found")

    if (server.metadata_ or {}).get("upgrade_excluded"):
        raise HTTPException(status_code=400, detail="This server is excluded from distro upgrades")

    current_distro = DistroUpgradeChecker.get_current_distro(server.os_type.value, server.os_version)
    target_distro = f"{server.os_type.value}-{request.target_version}".lower()
    if target_distro not in DistroUpgradeChecker.get_available_upgrades(current_distro):
        raise HTTPException(status_code=400, detail=f"Upgrade from {current_distro} to {target_distro} is not supported")

    from app.models.settings import SystemSettings
    from app.services.backup_providers import mapping_from_server

    settings_row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    if not (settings_row and (settings_row.backup_config or {}).get("enabled")):
        raise HTTPException(status_code=400, detail="A backup provider must be configured in Admin Settings before a distro upgrade")
    if not mapping_from_server(server.metadata_):
        raise HTTPException(status_code=400, detail="Map this server to its VM in Admin Settings before a distro upgrade")

    job = PatchJob(
        name=f"Distro upgrade {server.hostname} to {request.target_version}",
        patch_ids=[f"distro-upgrade:{target_distro}"],
        server_ids=[server.id],
        status=JobStatus.pending,
        dry_run=False,
        auto_rollback=False,
        metadata_={"type": "distro_upgrade", "target_version": request.target_version},
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    execution = PatchJobExecution(job_id=job.id, server_id=server.id, status=ExecutionStatus.pending)
    db.add(execution)
    await db.commit()
    await db.refresh(execution)

    task = execute_distro_upgrade.delay(
        str(server.id), request.target_version, request.acknowledge_risks, str(job.id), str(execution.id)
    )
    logger.info("distro_upgrade_queued", server_id=str(server.id), target=target_distro,
                job_id=str(job.id), task_id=task.id)

    return {
        "server_id": str(server.id),
        "hostname": server.hostname,
        "job_id": str(job.id),
        "task_id": task.id,
        "status": "queued",
        "message": "Upgrade queued. Progress is tracked in Jobs. The worker re-checks readiness before changing anything.",
    }


@router.get("/{server_id}/upgrade-status")
async def get_upgrade_status(
    server_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Check if system upgrade is in progress"""
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
                "upgrade_in_progress": False,
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
                "upgrade_in_progress": False,
                "message": "Failed to connect"
            }

        # Check for upgrade processes
        result = await connector.execute_command(
            "ps aux | grep -E 'apt|dpkg|update-manager' | grep -v grep",
            timeout=5
        )
        await connector.disconnect()

        upgrade_in_progress = result.success and result.stdout.strip()

        return {
            "server_id": str(server.id),
            "hostname": server.hostname,
            "upgrade_in_progress": upgrade_in_progress,
            "processes": result.stdout if upgrade_in_progress else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error("upgrade_status_error", server_id=server_id, error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to check upgrade status: {str(e)}")


async def upgrade_eligibility(db: AsyncSession, servers: List[Server]):
    """Split servers into those that can start a distro upgrade now and those that cannot, with a reason."""
    from app.models.settings import SystemSettings
    from app.services.backup_providers import mapping_from_server

    settings_row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    backup_ready = bool(settings_row and (settings_row.backup_config or {}).get("enabled"))

    eligible, excluded = [], []
    for server in servers:
        distro = DistroUpgradeChecker.get_current_distro(server.os_type.value, server.os_version)
        targets = DistroUpgradeChecker.get_available_upgrades(distro)
        reason = None
        if (server.metadata_ or {}).get("upgrade_excluded"):
            reason = "Excluded from distro upgrades"
        elif not targets:
            if distro in DistroUpgradeChecker.SUPPORTED_UPGRADES:
                reason = "Already on the latest supported release"
            else:
                reason = f"No upgrade path for {distro}"
        elif server.connectivity_status.value != "healthy":
            reason = "Server is not reachable"
        elif not backup_ready:
            reason = "No backup provider configured"
        elif not mapping_from_server(server.metadata_):
            reason = "Not mapped to a VM for backup"

        if reason:
            excluded.append({"id": str(server.id), "hostname": server.hostname, "reason": reason})
        else:
            eligible.append({
                "id": str(server.id),
                "hostname": server.hostname,
                "os_type": server.os_type.value,
                "os_version": server.os_version,
                "connectivity_status": server.connectivity_status.value,
                "available_upgrades": targets,
            })
    return eligible, excluded


@candidates_router.get("/candidates")
async def list_upgrade_candidates(db: AsyncSession = Depends(get_db)):
    """Servers that can start a distro upgrade now, plus the servers left out and why."""
    servers = (await db.execute(select(Server).order_by(Server.hostname))).scalars().all()
    candidates, excluded = await upgrade_eligibility(db, list(servers))
    return {"candidates": candidates, "excluded": excluded}
