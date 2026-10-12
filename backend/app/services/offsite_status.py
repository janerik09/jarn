"""
Whether the off-host copy of backups is protecting this installation. Used by the dashboard banner, the alert rules
and the admin screen. An installation with no off-host copy is only protected by its own host, so it is either
configured, or an administrator has recorded a waiver with a reason.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.settings import SystemSettings

MAX_AGE_HOURS = 36  # a daily run that has not succeeded for a day and a half is stale


def evaluate(config: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    last = config.get("last_run") or None
    waiver = config.get("waiver") or None
    base = {"last_run": last, "waiver": waiver}
    if not config.get("enabled"):
        if waiver:
            return {**base, "state": "waived", "problem": False,
                    "message": f"No off-host copy. Risk accepted by {waiver.get('by')} on {str(waiver.get('at'))[:10]}: {waiver.get('reason')}"}
        return {**base, "state": "off", "problem": True,
                "message": "Backups are only kept on this server. Turn on the off-host copy, or record a waiver with a reason."}
    if last is None:
        return {**base, "state": "never_run", "problem": True,
                "message": "The off-host copy is on but has never run. Press Copy now and check the result."}
    if not last.get("ok"):
        return {**base, "state": "failed", "problem": True,
                "message": f"The last off-host copy failed: {last.get('summary', 'see the audit log')}"}
    ran_at = datetime.fromisoformat(last["at"])
    if now - ran_at > timedelta(hours=MAX_AGE_HOURS):
        return {**base, "state": "stale", "problem": True,
                "message": f"The last successful off-host copy was {int((now - ran_at).total_seconds() // 3600)} hours ago."}
    return {**base, "state": "ok", "problem": False, "message": "Backups are copied off this server."}


async def status(db: AsyncSession) -> Dict[str, Any]:
    row = (await db.execute(select(SystemSettings).limit(1))).scalar_one_or_none()
    return evaluate(dict(row.offsite_config or {}) if row else {})
