import asyncio
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models import (
    PatchJob, PatchJobExecution, JobStatus, ExecutionStatus
)
from app.logging_config import get_logger

logger = get_logger(__name__)


class JobService:
    """Orchestrates patch job execution across multiple servers"""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_job(
        self,
        name: str,
        patch_ids: List[str],
        server_ids: List[str],
        priority: str = "medium",
        dry_run: bool = False,
        auto_rollback: bool = True,
        reboot_after: bool = False,
        graceful_reboot: bool = True,
        backup_before: bool = False,
        restore_on_failure: bool = False,
        created_by_id: str = None
    ) -> PatchJob:
        """Create a new patch job"""
        job = PatchJob(
            name=name,
            patch_ids=patch_ids,
            server_ids=server_ids,
            priority=priority,
            dry_run=dry_run,
            auto_rollback=auto_rollback,
            reboot_after=reboot_after,
            graceful_reboot=graceful_reboot,
            backup_before=backup_before,
            restore_on_failure=restore_on_failure,
            created_by_id=created_by_id,
            status=JobStatus.pending
        )
        self.db.add(job)
        await self.db.commit()
        await self.db.refresh(job)

        logger.info(
            "job_created",
            job_id=str(job.id),
            server_count=len(server_ids),
            patch_count=len(patch_ids)
        )

        return job

    async def get_job(self, job_id: str) -> Optional[PatchJob]:
        """Get job by ID"""
        result = await self.db.execute(
            select(PatchJob).where(PatchJob.id == job_id)
        )
        return result.scalar_one_or_none()

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Get comprehensive job status"""
        job = await self.get_job(job_id)
        if not job:
            return None

        # Get execution records
        result = await self.db.execute(
            select(PatchJobExecution).where(PatchJobExecution.job_id == job_id)
        )
        executions = result.scalars().all()

        # Calculate stats
        total = len(executions)
        succeeded = sum(1 for e in executions if e.status == ExecutionStatus.success)
        failed = sum(1 for e in executions if e.status == ExecutionStatus.failed)
        executing = sum(1 for e in executions if e.status == ExecutionStatus.executing)
        pending = sum(1 for e in executions if e.status == ExecutionStatus.pending)
        rolled_back = sum(1 for e in executions if e.status == ExecutionStatus.rolled_back)

        return {
            "job_id": str(job.id),
            "name": job.name,
            "metadata_": job.metadata_ or {},
            "status": job.status,
            "priority": job.priority,
            "created_at": job.created_at,
            "start_time": job.start_time,
            "end_time": job.end_time,
            "stats": {
                "total_servers": total,
                "succeeded": succeeded,
                "failed": failed,
                "executing": executing,
                "pending": pending,
                "rolled_back": rolled_back
            },
            "executions": [
                {
                    "server_id": str(e.server_id),
                    "status": e.status,
                    "duration_ms": self._calculate_duration(e.execution_start, e.execution_end),
                    "error": e.error_message,
                    "metadata_": e.metadata_
                }
                for e in executions
            ]
        }

    async def get_failed_servers(self, job_id: str) -> List[str]:
        """Get list of server IDs that failed in a job"""
        result = await self.db.execute(
            select(PatchJobExecution)
            .where(PatchJobExecution.job_id == job_id)
            .where(PatchJobExecution.status.in_([ExecutionStatus.failed, ExecutionStatus.rolled_back]))
        )
        executions = result.scalars().all()
        return [str(e.server_id) for e in executions]

        # TODO: Implement rollback logic
        # For now, just log it

    async def cancel_job(self, job_id: str) -> Dict[str, Any]:
        """Cancel a pending or running job"""
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if job.status not in [JobStatus.pending, JobStatus.running]:
            raise ValueError(f"Cannot cancel job with status {job.status}")

        job.status = JobStatus.cancelled
        job.end_time = datetime.utcnow()
        await self.db.commit()

        logger.info("job_cancelled", job_id=str(job_id))

        return await self.get_job_status(job_id)

    @staticmethod
    def _calculate_duration(start: datetime, end: datetime) -> int:
        """Calculate duration in milliseconds"""
        if not start or not end:
            return 0
        delta = end - start
        return int(delta.total_seconds() * 1000)
