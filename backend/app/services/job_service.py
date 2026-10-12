import asyncio
import uuid
from datetime import datetime
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.models import (
    PatchJob, PatchJobExecution, Server, Patch, User, JobStatus, ExecutionStatus
)
from app.connectors import SSHConnector
from app.connectors.ssh import pinned_host_key
from app.logging_config import get_logger
from app.models.server import OSType, ConnectivityStatus

logger = get_logger(__name__)


class JobService:
    """Orchestrates patch job execution across multiple servers"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.max_parallel_jobs = 10
        self.execution_semaphore = asyncio.Semaphore(self.max_parallel_jobs)

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

    async def execute_job(self, job_id: str) -> Dict[str, Any]:
        """Start job execution"""
        job = await self.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        job.status = JobStatus.running
        job.start_time = datetime.utcnow()
        await self.db.commit()

        logger.info("job_execution_started", job_id=str(job.id))

        # Create execution records for each server
        executions = []
        for server_id in job.server_ids:
            # Handle both string and UUID objects
            if isinstance(server_id, str):
                sid = uuid.UUID(server_id)
            else:
                sid = server_id

            execution = PatchJobExecution(
                job_id=job.id,
                server_id=sid,
                status=ExecutionStatus.pending
            )
            self.db.add(execution)
            executions.append(execution)

        await self.db.commit()

        # Start async execution in background with proper error handling
        task = asyncio.create_task(self._execute_job_async(job_id))

        def handle_task_exception(t):
            try:
                t.result()
            except Exception as e:
                logger.error("background_task_error", job_id=job_id, error=str(e), exc_info=True)

        task.add_done_callback(handle_task_exception)

        return await self.get_job_status(job_id)

    async def _execute_job_async(self, job_id: str):
        """Execute job asynchronously (runs in background)"""
        logger.info("async_job_execution_starting", job_id=job_id)

        # Create a new database session for background task
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            service = JobService(db)

            job = await service.get_job(job_id)
            if not job:
                logger.error("async_job_not_found", job_id=job_id)
                return

            try:
                # Get all execution records
                result = await db.execute(
                    select(PatchJobExecution)
                    .where(PatchJobExecution.job_id == job_id)
                    .where(PatchJobExecution.status == ExecutionStatus.pending)
                )
                executions = result.scalars().all()

                # Execute patches on each server concurrently
                tasks = [
                    service._execute_on_server(job, execution)
                    for execution in executions
                ]

                await asyncio.gather(*tasks)

                # Update job status
                all_executions = await db.execute(
                    select(PatchJobExecution).where(PatchJobExecution.job_id == job_id)
                )
                all_exec = all_executions.scalars().all()

                failed_count = sum(1 for e in all_exec if e.status == ExecutionStatus.failed)

                if failed_count == 0:
                    job.status = JobStatus.completed
                else:
                    job.status = JobStatus.completed

                    # Trigger auto-rollback if enabled
                    if job.auto_rollback and failed_count > 0:
                        await service._trigger_rollback(job_id)

                job.end_time = datetime.utcnow()
                await db.commit()

                logger.info("job_execution_completed", job_id=str(job.id))

            except Exception as e:
                logger.error("job_execution_error", job_id=str(job_id), error=str(e))
                job.status = JobStatus.failed
                job.end_time = datetime.utcnow()
                await db.commit()

    async def _execute_on_server(self, job: PatchJob, execution: PatchJobExecution):
        """Execute patches on a single server with retry logic"""
        logger.info("execute_on_server_waiting", server_id=str(execution.server_id))
        async with self.execution_semaphore:
            # Each parallel task gets its own database session
            from app.database import AsyncSessionLocal
            async with AsyncSessionLocal() as db:
                logger.info("execute_on_server_acquired", server_id=str(execution.server_id))
                try:
                    # Get server
                    result = await db.execute(select(Server).where(Server.id == execution.server_id))
                    server = result.scalar_one_or_none()

                    if not server:
                        execution.status = ExecutionStatus.failed
                        execution.error_message = "Server not found"
                        await db.merge(execution)
                        await db.commit()
                        return

                    execution.status = ExecutionStatus.executing
                    execution.execution_start = datetime.utcnow()
                    await db.merge(execution)
                    await db.commit()

                    # Execute patches with retry logic
                    result = await self._execute_with_retry(
                        job, server, execution, db
                    )

                    if result["success"]:
                        execution.status = ExecutionStatus.success
                        execution.output = result["output"]
                    else:
                        execution.status = ExecutionStatus.failed
                        execution.error_message = result["error"]

                except asyncio.TimeoutError:
                    execution.status = ExecutionStatus.failed
                    execution.error_message = "Execution timeout"
                    logger.error("execution_timeout", server_id=str(execution.server_id))
                except Exception as e:
                    execution.status = ExecutionStatus.failed
                    execution.error_message = str(e)
                    logger.error("execution_error", server_id=str(execution.server_id), error=str(e))
                finally:
                    execution.execution_end = datetime.utcnow()
                    await db.merge(execution)
                    await db.commit()

    async def _execute_with_retry(
        self, job: PatchJob, server: Server, execution: PatchJobExecution, db: AsyncSession = None
    ) -> Dict[str, Any]:
        """Execute patches with retry logic"""
        if db is None:
            db = self.db

        max_retries = 3
        retry_delay = 2  # seconds, will exponentially increase

        for attempt in range(max_retries):
            try:
                result = await self._patch_server(job, server)
                if result["success"]:
                    return result

                # Transient error, retry
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    logger.info(
                        "retrying_patch_execution",
                        server_id=str(server.id),
                        attempt=attempt + 1,
                        wait_seconds=wait_time
                    )
                    await asyncio.sleep(wait_time)
                else:
                    return result

            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    wait_time = retry_delay * (2 ** attempt)
                    await asyncio.sleep(wait_time)
                else:
                    raise

        return {"success": False, "error": "Max retries exceeded"}

    async def _patch_server(self, job: PatchJob, server: Server) -> Dict[str, Any]:
        """Connect to server and apply patches"""
        try:
            # For demo servers (no credentials), simulate the patching
            if not server.credentials or (not server.credentials.get("username") and not server.credentials.get("private_key")):
                # Simulate patch execution with realistic delays
                logger.info("demo_mode_patching", server_id=str(server.id), hostname=server.hostname)
                await asyncio.sleep(2)  # Simulate connection time
                await asyncio.sleep(len(job.patch_ids))  # Simulate patch installation (1s per patch)

                output = f"Successfully patched {server.hostname} with {len(job.patch_ids)} patches:\n"
                for patch_id in job.patch_ids:
                    output += f"  ✓ {patch_id}\n"
                return {"success": True, "output": output}

            # Real patching for Linux servers via SSH
            connector = SSHConnector(
                hostname=str(server.ip_address) if server.ip_address else server.hostname,
                username=server.credentials.get("username"),
                private_key=server.credentials.get("private_key"),
                password=server.credentials.get("password"),
                port=int(server.port) if server.port else 22,
                host_key=pinned_host_key(server.metadata_),
            )

            # Connect
            if not await asyncio.wait_for(connector.connect(), timeout=30):
                return {"success": False, "error": "Connection failed"}

            # Scan for current patches to get actual package names
            available_patches = await connector.scan_available_patches()

            # Map bundle names to actual packages
            actual_packages = []
            for patch_id in job.patch_ids:
                # Try exact match first
                matching = [p for p in available_patches if p.name == patch_id]

                # If not found, try flexible matching (handle bundle name variations)
                if not matching:
                    # Try matching by partial name (e.g., "Bundle 1" matches "Bundle 1-2 packages")
                    matching = [p for p in available_patches if patch_id in p.name or p.name in patch_id]

                if matching:
                    patch = matching[0]
                    # Extract package names from description (format: "Packages: pkg1, pkg2, +N more")
                    if "Packages:" in patch.description:
                        pkg_str = patch.description.split("Packages:")[1].strip()
                        # Remove "+N more" suffix if present
                        pkgs = [p.strip() for p in pkg_str.split(',') if not p.strip().startswith('+')]
                        actual_packages.extend(pkgs)
                    else:
                        logger.warning("patch_no_packages_found", patch_name=patch_id)

            if not actual_packages:
                # Fallback: if no packages found, try to use patch_ids as-is
                logger.warning("no_packages_matched_bundles", requested_bundles=job.patch_ids)
                actual_packages = job.patch_ids

            # Install patches using actual package names
            result = await asyncio.wait_for(
                connector.install_patches(actual_packages, dry_run=job.dry_run),
                timeout=300  # 5 minutes per server
            )

            await connector.disconnect()

            if result.success:
                return {"success": True, "output": result.stdout}
            else:
                return {"success": False, "error": result.stderr}

        except asyncio.TimeoutError:
            return {"success": False, "error": "Connection timeout"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def _trigger_rollback(self, job_id: str):
        """Trigger automatic rollback for failed servers"""
        job = await self.get_job(job_id)
        if not job:
            return

        # Get failed executions
        result = await self.db.execute(
            select(PatchJobExecution)
            .where(PatchJobExecution.job_id == job_id)
            .where(PatchJobExecution.status == ExecutionStatus.failed)
        )
        failed_executions = result.scalars().all()

        logger.info(
            "auto_rollback_triggered",
            job_id=str(job_id),
            failed_count=len(failed_executions)
        )

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
