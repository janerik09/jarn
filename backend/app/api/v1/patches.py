from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc
from app.database import get_db
from app.models import PatchJob, PatchJobExecution, Server
from app.services.patch_safety import PatchSafetyChecker
from app.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


@router.get("")
async def list_patches(db: AsyncSession = Depends(get_db)):
    """List all deployed patches from job history with safety analysis"""
    try:
        result = await db.execute(
            select(PatchJob).order_by(desc(PatchJob.created_at))
        )
        jobs = result.scalars().all()

        patches = []
        seen = set()

        for job in jobs:
            # Get executions for this job
            exec_result = await db.execute(
                select(PatchJobExecution).where(PatchJobExecution.job_id == job.id)
            )
            executions = exec_result.scalars().all()

            for patch_id in (job.patch_ids or []):
                patch_key = f"{patch_id}"
                if patch_key not in seen:
                    seen.add(patch_key)
                    success_count = sum(1 for e in executions if e.status.value == "success")

                    # Get safety analysis for this patch
                    safety_level, risk_factors = PatchSafetyChecker.check_package_name(str(patch_id), "")

                    patches.append({
                        "name": str(patch_id),
                        "deployed_on": job.created_at.isoformat(),
                        "job_id": str(job.id),
                        "job_name": job.name,
                        "success_count": success_count,
                        "total_servers": len(job.server_ids) if job.server_ids else 0,
                        "status": "deployed" if success_count > 0 else "pending",
                        "safety_level": safety_level.value,
                        "risk_factors": [rf.value for rf in risk_factors] if risk_factors else []
                    })

        return {
            "patches": patches,
            "total": len(patches)
        }
    except Exception as e:
        logger.error("list_patches_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/scan", status_code=202)
async def scan_patches(db: AsyncSession = Depends(get_db)):
    """Queue a patch scan of every server. Results appear on each server as the scans finish."""
    from app.tasks import rescan_servers

    result = await db.execute(select(Server.id))
    server_ids = [str(sid) for sid in result.scalars().all()]
    if server_ids:
        rescan_servers.delay(server_ids)
    logger.info("patch_scan_queued", servers=len(server_ids))
    return {"status": "queued", "servers_to_scan": len(server_ids)}


@router.get("/{patch_id}")
async def get_patch(patch_id: str, db: AsyncSession = Depends(get_db)):
    """Get patch deployment history with safety analysis"""
    try:
        result = await db.execute(select(PatchJob))
        jobs = result.scalars().all()

        patch_history = []
        for job in jobs:
            if patch_id in (job.patch_ids or []):
                exec_result = await db.execute(
                    select(PatchJobExecution).where(PatchJobExecution.job_id == job.id)
                )
                executions = exec_result.scalars().all()

                for execution in executions:
                    server_result = await db.execute(
                        select(Server).where(Server.id == execution.server_id)
                    )
                    server = server_result.scalar_one_or_none()

                    patch_history.append({
                        "server": server.hostname if server else "unknown",
                        "status": execution.status.value if hasattr(execution.status, 'value') else str(execution.status),
                        "deployed_at": job.created_at.isoformat(),
                        "error": execution.error_message
                    })

        if not patch_history:
            raise HTTPException(status_code=404, detail="Patch not found in job history")

        # Get safety analysis
        safety_level, risk_factors = PatchSafetyChecker.check_package_name(patch_id, "")
        recommendation = PatchSafetyChecker.get_patch_recommendation(patch_id)

        return {
            "id": patch_id,
            "name": patch_id,
            "history": patch_history,
            "total_deployments": len(patch_history),
            "safety": {
                "level": safety_level.value,
                "risk_factors": [rf.value for rf in risk_factors] if risk_factors else [],
                "recommendation": recommendation
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_patch_error", patch_id=patch_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
