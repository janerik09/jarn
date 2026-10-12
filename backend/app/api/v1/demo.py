from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.services.job_service import JobService
from app.models import Server, Patch, OSType, AuthMethod, PatchType, OSFamily
from app.models.server import ConnectivityStatus
from app.logging_config import get_logger
import uuid

logger = get_logger(__name__)
router = APIRouter()


@router.options("/demo/create-test-job")
async def options_demo_job():
    """CORS preflight for demo job creation"""
    return JSONResponse(
        content={},
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
        }
    )


@router.post("/demo/create-test-job")
async def create_demo_job(db: AsyncSession = Depends(get_db)):
    """Create a test patch job from existing servers"""
    try:
        # Get all existing servers
        result = await db.execute(select(Server).limit(10))
        demo_servers = result.scalars().all()

        if not demo_servers:
            return {
                "error": "No servers available",
                "message": "Please add at least one server before creating a job"
            }

        # Create demo patches if they don't exist
        demo_patches = []
        patch_names = [
            ("openssl", "3.0.8", PatchType.security, OSFamily.debian),
            ("kernel", "5.15.0-92", PatchType.critical, OSFamily.debian),
            ("curl", "7.68.0-1", PatchType.bugfix, OSFamily.debian),
        ]

        for pkg_name, version, patch_type, os_family in patch_names:
            # Check if patch exists
            result = await db.execute(
                select(Patch).where(
                    (Patch.package_name == pkg_name) &
                    (Patch.version == version)
                )
            )
            patch = result.scalar_one_or_none()

            if not patch:
                patch = Patch(
                    package_name=pkg_name,
                    version=version,
                    os_family=os_family,
                    patch_type=patch_type,
                    description=f"Patch for {pkg_name} v{version}",
                    cve_ids=[]
                )
                db.add(patch)

            demo_patches.append(patch)

        await db.commit()

        # Refresh to get IDs
        for patch in demo_patches:
            await db.refresh(patch)

        # Create job from existing servers
        service = JobService(db)
        job = await service.create_job(
            name=f"Test Job - {len(demo_servers)} servers",
            patch_ids=[str(p.id) for p in demo_patches],
            server_ids=[str(s.id) for s in demo_servers],
            priority="medium",
            dry_run=False,
            auto_rollback=True,
            created_by_id=None
        )

        logger.info("demo_job_created", job_id=str(job.id), server_count=len(demo_servers))

        # Auto-execute the demo job
        await service.execute_job(str(job.id))
        logger.info("demo_job_execution_started", job_id=str(job.id))

        return {
            "job_id": str(job.id),
            "name": job.name,
            "status": "running",
            "server_count": len(job.server_ids),
            "patch_count": len(job.patch_ids),
            "message": "Test job created and started!"
        }
    except Exception as e:
        logger.error("demo_job_creation_error", error=str(e), exc_info=True)
        return {
            "error": str(e),
            "message": "Failed to create test job"
        }
