from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from app.database import get_db
from app.models import Schedule
from app.logging_config import get_logger
from typing import List, Optional
from datetime import datetime, timedelta

logger = get_logger(__name__)
router = APIRouter()


class ScheduleCreate(BaseModel):
    name: str
    cron_expression: str
    timezone: str
    duration_minutes: int
    server_ids: List[str] = []
    patch_filters: dict = {}
    is_active: bool = True


@router.post("")
async def create_schedule(schedule: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    """Create a maintenance window schedule"""
    try:
        new_schedule = Schedule(
            name=schedule.name,
            cron_expression=schedule.cron_expression,
            timezone=schedule.timezone,
            duration_minutes=str(schedule.duration_minutes),
            server_ids=schedule.server_ids,
            patch_filters=schedule.patch_filters or {},
            is_active=schedule.is_active,
            created_by_id=None
        )
        db.add(new_schedule)
        await db.commit()
        await db.refresh(new_schedule)

        logger.info("schedule_created", schedule_id=str(new_schedule.id), name=schedule.name)

        return {
            "id": str(new_schedule.id),
            "name": new_schedule.name,
            "cron": new_schedule.cron_expression,
            "status": "active" if new_schedule.is_active else "inactive",
            "created_at": new_schedule.created_at.isoformat()
        }
    except Exception as e:
        logger.error("schedule_creation_error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.get("")
async def list_schedules(db: AsyncSession = Depends(get_db)):
    """List all maintenance schedules"""
    try:
        result = await db.execute(select(Schedule))
        schedules = result.scalars().all()

        return {
            "schedules": [
                {
                    "id": str(s.id),
                    "name": s.name,
                    "description": s.description,
                    "cron_expression": s.cron_expression,
                    "timezone": s.timezone,
                    "duration_minutes": s.duration_minutes,
                    "server_ids": [str(sid) for sid in (s.server_ids or [])],
                    "patch_filters": s.patch_filters or {},
                    "is_active": bool(s.is_active),
                    "action": "Installs all pending updates on the selected servers",
                    "last_run": ((s.metadata_ or {}).get("runs") or [None])[-1],
                    "created_at": s.created_at.isoformat(),
                    "updated_at": s.updated_at.isoformat() if s.updated_at else s.created_at.isoformat(),
                }
                for s in schedules
            ],
            "total": len(schedules)
        }
    except Exception as e:
        logger.error("list_schedules_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{schedule_id}")
async def get_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    """Get schedule details"""
    try:
        result = await db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )
        schedule = result.scalar_one_or_none()

        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        return {
            "id": str(schedule.id),
            "name": schedule.name,
            "cron": schedule.cron_expression,
            "timezone": schedule.timezone,
            "duration": schedule.duration_minutes,
            "servers": schedule.server_ids or [],
            "status": "active" if schedule.is_active else "inactive",
            "created_at": schedule.created_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_schedule_error", schedule_id=schedule_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{schedule_id}")
async def update_schedule(schedule_id: str, schedule_update: ScheduleCreate, db: AsyncSession = Depends(get_db)):
    """Update a schedule"""
    try:
        result = await db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )
        schedule = result.scalar_one_or_none()

        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")
        from app.services import audit as _audit
        _before = _audit.copy_json({"name": schedule.name, "cron_expression": schedule.cron_expression, "timezone": schedule.timezone,
                                    "server_ids": [str(x) for x in (schedule.server_ids or [])], "is_active": schedule.is_active})

        schedule.name = schedule_update.name
        schedule.cron_expression = schedule_update.cron_expression
        schedule.timezone = schedule_update.timezone
        schedule.duration_minutes = str(schedule_update.duration_minutes)
        schedule.server_ids = schedule_update.server_ids
        schedule.patch_filters = schedule_update.patch_filters or {}
        schedule.is_active = schedule_update.is_active

        await db.commit()
        _after = _audit.copy_json({"name": schedule.name, "cron_expression": schedule.cron_expression, "timezone": schedule.timezone,
                                   "server_ids": [str(x) for x in (schedule.server_ids or [])], "is_active": schedule.is_active})
        await _audit.record('config.update', category='schedule', resource_type='schedule', resource_id=schedule_id,
                            details={'changes': _audit.diff(_before, _after)})

        logger.info("schedule_updated", schedule_id=schedule_id)

        return {
            "id": str(schedule.id),
            "name": schedule.name,
            "updated": True
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_schedule_error", schedule_id=schedule_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{schedule_id}")
async def delete_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    """Delete a schedule"""
    try:
        result = await db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )
        schedule = result.scalar_one_or_none()

        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        await db.delete(schedule)
        await db.commit()

        logger.info("schedule_deleted", schedule_id=schedule_id)

        return {"id": schedule_id, "deleted": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_schedule_error", schedule_id=schedule_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{schedule_id}/test")
async def test_schedule(schedule_id: str, db: AsyncSession = Depends(get_db)):
    """Preview when schedule runs next"""
    try:
        result = await db.execute(
            select(Schedule).where(Schedule.id == schedule_id)
        )
        schedule = result.scalar_one_or_none()

        if not schedule:
            raise HTTPException(status_code=404, detail="Schedule not found")

        # Simple next run calculation (demo)
        next_run = datetime.utcnow() + timedelta(days=7)

        return {
            "schedule_id": schedule_id,
            "name": schedule.name,
            "next_run": next_run.isoformat(),
            "timezone": schedule.timezone,
            "duration_minutes": schedule.duration_minutes
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("test_schedule_error", schedule_id=schedule_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
