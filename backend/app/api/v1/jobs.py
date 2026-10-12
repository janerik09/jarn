from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models import PatchJob, PatchJobExecution, Server, JobStatus
from app.services.job_service import JobService
from app.connectors.ssh import pinned_host_key
from app.services.preflight_service import PreflightChecker
from app.logging_config import get_logger
from app.tasks import execute_patch_job
import asyncio
import json

logger = get_logger(__name__)
router = APIRouter()


class CreateJobRequest(BaseModel):
    name: str
    patch_ids: List[str]
    server_ids: List[str]
    priority: Optional[str] = "medium"
    dry_run: Optional[bool] = False
    auto_rollback: Optional[bool] = True
    reboot_after: Optional[bool] = False  # Reboot servers if patches require it
    graceful_reboot: Optional[bool] = True  # Use graceful reboot (shutdown -r) vs immediate (reboot)
    backup_before: Optional[bool] = False  # Back up each server before patching
    restore_on_failure: Optional[bool] = False  # Restore the VM if rollback does not recover the server
    approved_risks: Optional[bool] = False  # Operator approved the impact warnings from the analysis
    deployment_strategy: Optional[str] = "all"  # all | canary | staged
    canary_percent: Optional[int] = 10


@router.post("")
async def create_job(
    request: CreateJobRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create a new patch job and start execution in background"""
    try:
        if request.backup_before or request.restore_on_failure:
            from app.models.settings import SystemSettings
            from app.services.backup_providers import mapping_from_server

            settings_row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
            if not (settings_row and (settings_row.backup_config or {}).get("enabled")):
                raise HTTPException(status_code=400, detail="Configure a backup provider in Admin Settings first")
            servers = (await db.execute(select(Server).where(Server.id.in_(request.server_ids)))).scalars().all()
            unmapped = [s.hostname for s in servers if not mapping_from_server(s.metadata_)]
            if unmapped:
                raise HTTPException(status_code=400, detail=f"Map these servers to their VM in Admin Settings: {', '.join(unmapped)}")

        if request.deployment_strategy not in ("all", "canary", "staged"):
            raise HTTPException(status_code=400, detail="deployment_strategy must be all, canary or staged")
        if not 1 <= (request.canary_percent or 10) <= 50:
            raise HTTPException(status_code=400, detail="canary_percent must be between 1 and 50")
        analysis = []
        window = {"configured": False, "inside": True}
        if request.patch_ids and request.server_ids:
            from app.models.settings import SystemSettings
            from app.services.maintenance import window_status
            from app.services.risk_analysis import analyze_job

            settings_row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
            window = window_status(settings_row) if settings_row else window

            servers = (await db.execute(select(Server).where(Server.id.in_(request.server_ids)))).scalars().all()
            analysis = await analyze_job([
                {
                    "id": str(s.id),
                    "hostname": s.hostname,
                    "ip": str(s.ip_address) if s.ip_address else None,
                    "port": int(s.port) if s.port else 22,
                    "creds": dict(s.credentials or {}),
                    "host_key": pinned_host_key(s.metadata_),
                }
                for s in servers
            ], request.patch_ids)

            outside_window = window["configured"] and not window["inside"]
            flagged = [a for a in analysis if a.get("requires_approval") or a.get("drain_required")
                       or a.get("unresolved") or a.get("rollback_unavailable") or a.get("removals")]
            k8s_order = [a["hostname"] for a in analysis if a.get("drain_required")]
            if (flagged or outside_window) and not request.approved_risks:
                reasons = []
                if outside_window:
                    reasons.append(f"This job would run outside the maintenance window ({window['window']}; it is now {window['now']}).")
                if flagged:
                    reasons.append("It affects running applications, Kubernetes nodes, removes packages, or includes packages that cannot be rolled back.")
                raise HTTPException(status_code=409, detail={
                    "message": " ".join(reasons) + " Review and approve to continue.",
                    "servers": analysis,
                    "kubernetes_order": k8s_order,
                    "outside_window": outside_window,
                    "maintenance_window": window,
                })

        service = JobService(db)
        job = await service.create_job(
            name=request.name,
            patch_ids=request.patch_ids,
            server_ids=request.server_ids,
            priority=request.priority,
            dry_run=request.dry_run,
            auto_rollback=request.auto_rollback,
            reboot_after=request.reboot_after,
            backup_before=request.backup_before,
            restore_on_failure=request.restore_on_failure,
            graceful_reboot=request.graceful_reboot,
            created_by_id=None  # TODO: Get from auth context
        )

        job.metadata_ = {**(job.metadata_ or {}), "deployment": {
            "strategy": request.deployment_strategy,
            "canary_percent": request.canary_percent or 10,
        }}
        await db.commit()

        if analysis:
            job.metadata_ = {
                **(job.metadata_ or {}),
                "risk_analysis": analysis,
                "risks_approved": bool(request.approved_risks),
                "outside_maintenance_window": bool(window.get("configured") and not window.get("inside")),
                "maintenance_window": window,
                "kubernetes_order": [a["hostname"] for a in analysis if a.get("drain_required")],
            }
            await db.commit()

        return {
            "id": str(job.id),
            "name": job.name,
            "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
            "server_count": len(job.server_ids),
            "patch_count": len(job.patch_ids),
            "created_at": job.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("job_creation_error", error=str(e), traceback=True)
        raise HTTPException(status_code=400, detail=f"Failed to create job: {str(e)}")


@router.get("")
async def list_jobs(db: AsyncSession = Depends(get_db)):
    """List patch jobs"""
    try:
        result = await db.execute(
            select(PatchJob).order_by(PatchJob.created_at.desc())
        )
        jobs = result.scalars().all()

        return {
            "jobs": [
                {
                    "id": str(job.id),
                    "name": job.name,
                    "status": job.status.value if hasattr(job.status, 'value') else str(job.status),
                    "server_count": len(job.server_ids) if job.server_ids else 0,
                    "patch_count": len(job.patch_ids) if job.patch_ids else 0,
                    "priority": job.priority.value if hasattr(job.priority, 'value') else str(job.priority),
                    "dry_run": job.dry_run,
                    "created_at": job.created_at.isoformat(),
                    "start_time": job.start_time.isoformat() if job.start_time else None,
                    "end_time": job.end_time.isoformat() if job.end_time else None,
                }
                for job in jobs
            ],
            "total": len(jobs)
        }
    except Exception as e:
        logger.error("list_jobs_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}")
async def get_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Get job details and status"""
    try:
        service = JobService(db)
        status = await service.get_job_status(job_id)

        if not status:
            raise HTTPException(status_code=404, detail="Job not found")

        return status
    except Exception as e:
        logger.error("get_job_error", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{job_id}/executions")
async def get_job_executions(job_id: str, server_id: str = None, db: AsyncSession = Depends(get_db)):
    """Get execution details for a job, optionally filtered by server"""
    try:
        query = select(PatchJobExecution).where(PatchJobExecution.job_id == job_id)

        if server_id:
            query = query.where(PatchJobExecution.server_id == server_id)

        result = await db.execute(query)
        executions = result.scalars().all()

        return {
            "executions": [
                {
                    "id": str(e.id),
                    "job_id": str(e.job_id),
                    "server_id": str(e.server_id),
                    "status": e.status.value,
                    "execution_start": e.execution_start.isoformat() if e.execution_start else None,
                    "execution_end": e.execution_end.isoformat() if e.execution_end else None,
                    "output": e.output,
                    "error_message": e.error_message,
                    "metadata_": e.metadata_,
                }
                for e in executions
            ]
        }
    except Exception as e:
        logger.error("get_executions_error", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/execute")
async def execute_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Queue a patch job for execution"""
    try:
        # Verify job exists
        result = await db.execute(
            select(PatchJob).where(PatchJob.id == job_id)
        )
        job = result.scalar_one_or_none()

        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status != JobStatus.pending:
            raise HTTPException(status_code=409, detail=f"This job is {job.status.value}, so it cannot be started again")

        # A server can only be in one patch run at a time: refuse if a running job includes any of its servers.
        # Drafts that were never started do not hold servers; the per-server lock catches them if they start later.
        others = (await db.execute(select(PatchJob).where(
            PatchJob.id != job.id, PatchJob.status == JobStatus.running))).scalars().all()
        mine = {str(s) for s in job.server_ids}
        for other in others:
            overlap = mine & {str(s) for s in (other.server_ids or [])}
            if overlap:
                raise HTTPException(status_code=409, detail=f"Job '{other.name}' is running on {len(overlap)} of these servers. Wait for it to finish.")

        # Queue the job with Celery
        task = execute_patch_job.delay(job_id)

        logger.info("job_queued", job_id=job_id, task_id=task.id)

        return {
            "id": str(job.id),
            "name": job.name,
            "status": "queued",
            "task_id": task.id,
            "message": "Job queued for execution"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("job_queue_error", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Cancel a running job"""
    try:
        service = JobService(db)
        result = await service.cancel_job(job_id)

        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("cancel_job_error", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


class RollbackRequest(BaseModel):
    confirm: bool = False  # False returns a preview of what would change; True starts the rollback


@router.post("/{job_id}/rollback")
async def rollback_job(job_id: str, request: Optional[RollbackRequest] = None, db: AsyncSession = Depends(get_db)):
    """Roll back the packages a finished job changed, to the versions recorded before the run.

    Without confirm this returns a preview and changes nothing. With confirm the rollback runs in the background.
    """
    from app.services.job_rollback import plan_job_rollback
    from app.tasks import _job_rollback_items, rollback_job as rollback_task

    job = await db.get(PatchJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (JobStatus.completed, JobStatus.rolled_back):
        raise HTTPException(status_code=400, detail="Only a finished job can be rolled back")
    _, items = await _job_rollback_items(job_id)
    plan = plan_job_rollback(items)
    confirm = bool(request and request.confirm)
    if not confirm:
        return {
            "preview": True,
            "eligible": [{"hostname": i["hostname"], "packages": i["packages"],
                          "kernel_left_installed": i["kernel_left_installed"]} for i in plan["eligible"]],
            "skipped": [{"hostname": i["hostname"], "reason": i["reason"]} for i in plan["skipped"]],
        }
    if not plan["eligible"]:
        raise HTTPException(status_code=400, detail="No server in this job can be rolled back")
    task = rollback_task.delay(job_id)
    logger.info("job_rollback_queued", job_id=job_id, servers=len(plan["eligible"]), task_id=task.id)
    return {"task_id": task.id, "status": "queued", "servers": len(plan["eligible"])}


@router.post("/{job_id}/retry-failed")
async def retry_failed_job(job_id: str, db: AsyncSession = Depends(get_db)):
    """Retry a job but only for servers that failed"""
    try:
        # Get original job
        result = await db.execute(select(PatchJob).where(PatchJob.id == job_id))
        original_job = result.scalar_one_or_none()

        if not original_job:
            raise HTTPException(status_code=404, detail="Job not found")

        service = JobService(db)

        # Get failed server IDs from job execution records
        failed_servers = await service.get_failed_servers(job_id)

        if not failed_servers:
            raise HTTPException(status_code=400, detail="No failed servers found in this job")

        # Create new job with only failed servers
        retry_job = await service.create_job(
            name=f"{original_job.name} - Retry",
            patch_ids=original_job.patch_ids,
            server_ids=failed_servers,
            priority=original_job.priority.value if hasattr(original_job.priority, 'value') else str(original_job.priority),
            dry_run=original_job.dry_run,
            auto_rollback=original_job.auto_rollback,
            created_by_id=original_job.created_by_id
        )

        # Queue the retry job with Celery
        task = execute_patch_job.delay(str(retry_job.id))
        logger.info("retry_job_queued", job_id=str(retry_job.id), task_id=task.id)

        return {
            "id": str(retry_job.id),
            "name": retry_job.name,
            "status": retry_job.status.value if hasattr(retry_job.status, 'value') else str(retry_job.status),
            "server_count": len(retry_job.server_ids),
            "patch_count": len(retry_job.patch_ids),
            "failed_servers": failed_servers,
            "created_at": retry_job.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("retry_failed_error", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{job_id}/preflight-check")
async def preflight_check(job_id: str, db: AsyncSession = Depends(get_db)):
    """Check if servers are ready for patching"""
    try:
        checker = PreflightChecker(db)
        result = await checker.check_job_readiness(job_id)

        return result
    except Exception as e:
        logger.error("preflight_error", job_id=job_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.websocket("/ws/{job_id}/monitor")
async def job_monitor_stream(websocket: WebSocket, job_id: str):
    """Real-time job progress stream"""
    await websocket.accept()

    try:
        # Import here to get fresh connection each iteration
        from app.database import AsyncSessionLocal

        last_sent = None
        finished = {"completed", "failed", "rolled_back", "cancelled"}
        while True:
            # Fetch real job status from database
            async with AsyncSessionLocal() as db:
                service = JobService(db)
                status = await service.get_job_status(job_id)

            if status:
                # Transform stats to match frontend expectations
                stats = status.get("stats", {})
                transformed_stats = {
                    "total": stats.get("total_servers", 0),
                    "succeeded": stats.get("succeeded", 0),
                    "failed": stats.get("failed", 0) + stats.get("rolled_back", 0),
                    "rolled_back": stats.get("rolled_back", 0),
                    "executing": stats.get("executing", 0),
                    "pending": stats.get("pending", 0),
                }

                # Transform executions to match frontend expectations
                servers = []
                for exec_info in status.get("executions", []):
                    # Extract phases from metadata if available
                    phases = []
                    metadata = exec_info.get("metadata_", {})
                    if isinstance(metadata, dict) and "phases" in metadata:
                        phases = metadata.get("phases", [])

                    servers.append({
                        "server_id": exec_info.get("server_id"),
                        "hostname": exec_info.get("server_id"),  # Will be replaced with actual hostname if available
                        "status": exec_info.get("status"),
                        "duration_ms": exec_info.get("duration_ms"),
                        "error": exec_info.get("error"),
                        "phases": phases
                    })

                update = {
                    "type": "progress",
                    "job_id": job_id,
                    "timestamp": __import__('datetime').datetime.utcnow().isoformat(),
                    "job_name": status.get("name"),
                    "job_status": status.get("status"),
                    "awaiting_continue": bool((status.get("metadata_") or {}).get("awaiting_continue")),
                    "stats": transformed_stats,
                    "servers": servers
                }
            else:
                update = {
                    "type": "error",
                    "job_id": job_id,
                    "message": "Job not found"
                }

            comparable = json.dumps({k: v for k, v in update.items() if k != "timestamp"}, sort_keys=True, default=str)
            if comparable != last_sent:
                await websocket.send_json(update)
                last_sent = comparable

            if status and str(status.get("status")) in finished:
                break
            await asyncio.sleep(2)

    except Exception as e:
        logger.error("websocket_error", job_id=job_id, error=str(e))
    finally:
        await websocket.close()


@router.post("/{job_id}/continue", status_code=202)
async def continue_rollout(job_id: str, db: AsyncSession = Depends(get_db)):
    """Continue a rollout that paused after the canary. Only the servers still pending are patched."""
    from app.tasks import execute_patch_job

    job = (await db.execute(select(PatchJob).where(PatchJob.id == job_id))).scalar_one_or_none()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    metadata = dict(job.metadata_ or {})
    if not metadata.get("awaiting_continue"):
        raise HTTPException(status_code=409, detail="This job is not waiting to continue")
    metadata.pop("awaiting_continue", None)
    metadata.pop("awaiting_since", None)
    metadata["continued_at"] = datetime.utcnow().isoformat()
    job.metadata_ = metadata
    await db.commit()

    execute_patch_job.delay(job_id, True)
    logger.info("job_rollout_continued", job_id=job_id)
    return {"job_id": job_id, "status": "continuing"}
