"""
Runs scheduled patch jobs. A schedule creates a normal job, so the same validation applies as for a job you create:
approval is still required for anything risky, and jobs outside the maintenance window are refused.
A run that would need approval is recorded as skipped and reported through the notification webhook.
"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from app.database import AsyncSessionLocal
from app.logging_config import get_logger
from app.models import PatchJob, JobStatus
from app.models.job import Schedule
from app.services import cron, notify

logger = get_logger(__name__)
MAX_RUNS_KEPT = 20


async def _record(schedule: Schedule, result: str, detail: str, job_id: str = None) -> None:
    from app.services import audit
    await audit.record(
        "schedule.run", category="schedule", resource_type="schedule", resource_id=str(schedule.id),
        outcome={"started": "success", "skipped": "info"}.get(result, "failure"),
        source="scheduler", reason=detail if result != "started" else None,
        correlation_id=job_id or str(schedule.id),
        details={"schedule": schedule.name, "result": result, "job_id": job_id},
    )
    metadata = dict(schedule.metadata_ or {})
    runs = list(metadata.get("runs", []))
    runs.append({"at": datetime.utcnow().isoformat(), "result": result, "detail": detail, "job_id": job_id})
    metadata["runs"] = runs[-MAX_RUNS_KEPT:]
    schedule.metadata_ = metadata
    flag_modified(schedule, "metadata_")


async def _active_run_exists(db, schedule_id: str) -> bool:
    rows = (await db.execute(
        select(PatchJob.id).where(
            PatchJob.status.in_([JobStatus.pending, JobStatus.running]),
            PatchJob.metadata_["schedule_id"].as_string() == schedule_id,
        )
    )).scalars().all()
    return bool(rows)


async def run_due(now: datetime = None) -> dict:
    from app.api.v1.jobs import CreateJobRequest, create_job
    from app.tasks import execute_patch_job, ALL_UPGRADABLE

    now = now or datetime.now(timezone.utc)
    started, skipped = [], []
    async with AsyncSessionLocal() as db:
        schedules = (await db.execute(select(Schedule).where(Schedule.is_active.is_(True)))).scalars().all()
        for schedule in schedules:
            try:
                local = now.astimezone(ZoneInfo(schedule.timezone or "UTC"))
                if not cron.matches(schedule.cron_expression, local):
                    continue
            except (ValueError, KeyError) as e:
                await _record(schedule, "failed", f"Invalid schedule: {e}")
                await db.commit()
                continue

            minute_key = local.strftime("%Y%m%d%H%M")
            if (schedule.metadata_ or {}).get("last_minute") == minute_key:
                continue
            schedule.metadata_ = {**(schedule.metadata_ or {}), "last_minute": minute_key}
            flag_modified(schedule, "metadata_")
            await db.commit()  # Mark this minute as handled before anything else, so it fires once

            if not schedule.server_ids:
                await _record(schedule, "skipped", "No servers selected")
                skipped.append(schedule.name)
                await db.commit()
                continue
            if await _active_run_exists(db, str(schedule.id)):
                await _record(schedule, "skipped", "The previous run is still in progress")
                skipped.append(schedule.name)
                await db.commit()
                continue

            request = CreateJobRequest(
                name=f"Scheduled: {schedule.name} ({local:%Y-%m-%d %H:%M})",
                patch_ids=[ALL_UPGRADABLE],
                server_ids=[str(sid) for sid in schedule.server_ids],
            )
            try:
                created = await create_job(request, db)
            except HTTPException as e:
                detail = e.detail.get("message", "Needs approval") if isinstance(e.detail, dict) else str(e.detail)
                await _record(schedule, "skipped", detail)
                skipped.append(schedule.name)
                await db.commit()
                await notify.send("schedule_skipped", f"Scheduled run skipped: {schedule.name}",
                                  f"{detail} Run it manually from Create Job after review.")
                continue

            job = (await db.execute(select(PatchJob).where(PatchJob.id == created["id"]))).scalar_one()
            job.metadata_ = {**(job.metadata_ or {}), "schedule_id": str(schedule.id)}
            await _record(schedule, "started", "Patch job queued", job_id=created["id"])
            await db.commit()
            execute_patch_job.delay(created["id"])
            started.append(schedule.name)
            logger.info("scheduled_job_started", schedule=schedule.name, job_id=created["id"])
    return {"started": started, "skipped": skipped}
