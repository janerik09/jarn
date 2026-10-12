"""
Checks that the platform itself is working: worker reachable, database backups fresh, restore check recent,
compliance assessment running. Used by /health, /metrics and the alert task. Read only.
"""

import asyncio
import os
import time
from typing import Any, Dict, List, Optional

from app.logging_config import get_logger

logger = get_logger(__name__)

BACKUP_DIR = os.environ.get("BACKUP_DIR", "/backups")
MAX_BACKUP_AGE_HOURS = 36
MAX_RESTORE_CHECK_AGE_DAYS = 14
MAX_COMPLIANCE_AGE_HOURS = 36


def worker_replies(timeout: float = 1.5) -> int:
    from app.celery_app import app as celery_app
    replies = celery_app.control.ping(timeout=timeout) or []
    return len(replies)


async def worker_count(timeout: float = 1.5) -> int:
    return await asyncio.to_thread(worker_replies, timeout)


def _newest_backup_age_seconds() -> Optional[float]:
    try:
        dumps = [os.path.join(BACKUP_DIR, f) for f in os.listdir(BACKUP_DIR)
                 if f.startswith("patching_db-") and f.endswith(".dump")]
    except OSError:
        return None
    if not dumps:
        return None
    return time.time() - max(os.path.getmtime(d) for d in dumps)


def _marker_age_seconds(name: str) -> Optional[float]:
    path = os.path.join(BACKUP_DIR, name)
    try:
        return time.time() - os.path.getmtime(path)
    except OSError:
        return None


async def _latest_compliance_age_seconds() -> Optional[float]:
    from sqlalchemy import select
    from app.database import AsyncSessionLocal
    from app.models.compliance import ComplianceAssessment
    from datetime import datetime
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(ComplianceAssessment.completed_at)
            .where(ComplianceAssessment.status == "completed")
            .order_by(ComplianceAssessment.completed_at.desc()).limit(1)
        )).scalar_one_or_none()
    if row is None:
        return None
    return (datetime.utcnow() - row).total_seconds()


async def snapshot() -> Dict[str, Any]:
    """Current state of each platform check, with a problem description where one exists."""
    workers = await worker_count()
    backup_age = await asyncio.to_thread(_newest_backup_age_seconds)
    restore_age = await asyncio.to_thread(_marker_age_seconds, ".last_restore_check")
    compliance_age = await _latest_compliance_age_seconds()
    from app.database import AsyncSessionLocal
    from app.services import offsite_status
    async with AsyncSessionLocal() as db:
        offsite = await offsite_status.status(db)
    problems: Dict[str, str] = {}
    if offsite["problem"]:
        problems["offsite_copy"] = offsite["message"]
    if workers == 0:
        problems["worker_down"] = "No Celery worker is responding. Patch jobs and scheduled runs will not start."
    if backup_age is None:
        problems["backup_missing"] = "No database backup was found."
    elif backup_age > MAX_BACKUP_AGE_HOURS * 3600:
        problems["backup_stale"] = f"The newest database backup is {int(backup_age // 3600)} hours old."
    if restore_age is None or restore_age > MAX_RESTORE_CHECK_AGE_DAYS * 86400:
        problems["restore_check_stale"] = "The backup restore check has not passed in the last 14 days."
    if compliance_age is None:
        problems["compliance_stale"] = "No compliance assessment has completed yet."
    elif compliance_age > MAX_COMPLIANCE_AGE_HOURS * 3600:
        problems["compliance_stale"] = f"The last compliance assessment finished {int(compliance_age // 3600)} hours ago."
    return {
        "workers": workers,
        "backup_age_seconds": backup_age,
        "restore_check_age_seconds": restore_age,
        "compliance_age_seconds": compliance_age,
        "problems": problems,
    }


async def evaluate_and_notify() -> Dict[str, Any]:
    """Send each new problem once, and a resolved message when it clears. Redis remembers what was sent."""
    import redis as sync_redis
    from app.config import settings
    from app.services import notify
    state = await snapshot()
    redis = sync_redis.from_url(settings.redis_url)
    raised: List[str] = []
    resolved: List[str] = []
    all_keys = ["worker_down", "backup_missing", "backup_stale", "restore_check_stale", "compliance_stale", "offsite_copy"]
    for key in all_keys:
        active = key in state["problems"]
        flag = f"jarn:alert:{key}"
        was_active = bool(redis.exists(flag))
        if active and not was_active:
            redis.set(flag, "1")
            raised.append(key)
            from app.services import audit as _audit
            await _audit.record('alert.raised', category='system', source='scheduler', outcome='failure', reason=state['problems'][key], details={'alert': key})
            await notify.send(f"alert_{key}", f"JÁRN alert: {key.replace('_', ' ')}", state["problems"][key])
        elif not active and was_active:
            redis.delete(flag)
            resolved.append(key)
            from app.services import audit as _audit
            await _audit.record('alert.resolved', category='system', source='scheduler', outcome='info', details={'alert': key})
            await notify.send(f"resolved_{key}", f"JÁRN resolved: {key.replace('_', ' ')}", "The problem has cleared.")
    if raised or resolved:
        logger.warning("alerts_changed", raised=raised, resolved=resolved)
    return {"raised": raised, "resolved": resolved, "problems": state["problems"]}
