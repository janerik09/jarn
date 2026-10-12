"""
Audit trail: search, summary, export and integrity check. Viewers can search and check; exporting is admin only.
"""

import csv
import io
import json
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import func, or_, select, cast, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import admin_only
from app.database import get_db
from app.models.audit import AuditLog
from app.services import audit as audit_service

router = APIRouter()
COLUMNS = ["seq", "created_at", "category", "action", "outcome", "source", "actor_name", "actor_id",
           "resource_type", "resource_id", "correlation_id", "ip_address", "reason", "details"]


def _filtered(query, category, outcome, actor, action, resource_type, correlation_id, source, q, since, until):
    if category:
        query = query.where(AuditLog.category == category)
    if outcome:
        query = query.where(AuditLog.outcome == outcome)
    if actor:
        query = query.where(AuditLog.actor_name.ilike(f"%{actor}%"))
    if action:
        query = query.where(AuditLog.action.ilike(f"%{action}%"))
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
    if correlation_id:
        query = query.where(AuditLog.correlation_id == correlation_id)
    if source:
        query = query.where(AuditLog.source == source)
    if q:
        needle = f"%{q}%"
        query = query.where(or_(AuditLog.action.ilike(needle), AuditLog.reason.ilike(needle),
                                AuditLog.actor_name.ilike(needle), cast(AuditLog.details, String).ilike(needle)))
    if since:
        query = query.where(AuditLog.created_at >= since)
    if until:
        query = query.where(AuditLog.created_at <= until)
    return query


def _row(r: AuditLog) -> dict:
    return {
        "seq": r.seq,
        "id": str(r.id),
        "created_at": r.created_at.isoformat(),
        "category": r.category,
        "action": r.action,
        "outcome": r.outcome or (r.status.value if r.status else None),
        "source": r.source,
        "actor_name": r.actor_name,
        "actor_id": str(r.actor_id) if r.actor_id else None,
        "resource_type": r.resource_type,
        "resource_id": str(r.resource_id) if r.resource_id else None,
        "correlation_id": r.correlation_id,
        "ip_address": str(r.ip_address) if r.ip_address else None,
        "reason": r.reason,
        "details": r.details,
        "row_hash": r.row_hash,
    }


def _filters(category=None, outcome=None, actor=None, action=None, resource_type=None, correlation_id=None,
             source=None, q=None, since: Optional[datetime] = None, until: Optional[datetime] = None):
    return dict(category=category, outcome=outcome, actor=actor, action=action, resource_type=resource_type,
                correlation_id=correlation_id, source=source, q=q, since=since, until=until)


@router.get("")
async def search(
    category: Optional[str] = None, outcome: Optional[str] = None, actor: Optional[str] = None,
    action: Optional[str] = None, resource_type: Optional[str] = None, correlation_id: Optional[str] = None,
    source: Optional[str] = None, q: Optional[str] = None, since: Optional[datetime] = None,
    until: Optional[datetime] = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    f = _filters(category, outcome, actor, action, resource_type, correlation_id, source, q, since, until)
    base = _filtered(select(AuditLog), **f)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await db.execute(base.order_by(AuditLog.seq.desc()).offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return {"items": [_row(r) for r in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/summary")
async def summary(days: int = Query(14, ge=1, le=90), db: AsyncSession = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=days)
    day = func.date_trunc("day", AuditLog.created_at)
    by_day = (await db.execute(
        select(day, AuditLog.outcome, func.count())
        .where(AuditLog.created_at >= since)
        .group_by(day, AuditLog.outcome).order_by(day)
    )).all()
    day_counts: dict = {}
    for d, outcome, n in by_day:
        bucket = day_counts.setdefault(d.date().isoformat(), {})
        bucket[outcome or "unknown"] = n
    failures = (await db.execute(
        select(AuditLog.action, AuditLog.category, func.count())
        .where(AuditLog.created_at >= since, AuditLog.outcome.in_(["failure", "denied"]))
        .group_by(AuditLog.action, AuditLog.category).order_by(func.count().desc()).limit(10)
    )).all()
    day_ago = datetime.utcnow() - timedelta(hours=24)
    failed_sign_ins = (await db.execute(
        select(func.count()).where(AuditLog.action == "POST /api/v1/auth/login",
                                   AuditLog.outcome == "failure", AuditLog.created_at >= day_ago)
    )).scalar_one()
    denied_24h = (await db.execute(
        select(func.count()).where(AuditLog.outcome == "denied", AuditLog.created_at >= day_ago)
    )).scalar_one()
    return {
        "days": days,
        "by_day": day_counts,
        "top_failures": [{"action": a, "category": c, "count": n} for a, c, n in failures],
        "failed_sign_ins_24h": failed_sign_ins,
        "denied_24h": denied_24h,
    }


@router.get("/verify")
async def verify():
    """Re-checks the hash chain. A changed, deleted or inserted event shows up as the first break."""
    return await audit_service.verify_chain()


@router.get("/export", dependencies=[Depends(admin_only())])
async def export(
    format: str = Query("csv", pattern="^(csv|json)$"),
    category: Optional[str] = None, outcome: Optional[str] = None, actor: Optional[str] = None,
    action: Optional[str] = None, resource_type: Optional[str] = None, correlation_id: Optional[str] = None,
    source: Optional[str] = None, q: Optional[str] = None, since: Optional[datetime] = None,
    until: Optional[datetime] = None, db: AsyncSession = Depends(get_db),
):
    f = _filters(category, outcome, actor, action, resource_type, correlation_id, source, q, since, until)
    rows = (await db.execute(_filtered(select(AuditLog), **f).order_by(AuditLog.seq).limit(100000))).scalars().all()
    await audit_service.record("audit.export", category="audit", resource_type="audit",
                               details={"format": format, "rows": len(rows), "filters": {k: str(v) for k, v in f.items() if v}})
    items = [_row(r) for r in rows]
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    if format == "json":
        return Response(json.dumps(items, indent=2, default=str), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.json"'})
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(COLUMNS + ["row_hash"])
    for item in items:
        writer.writerow([json.dumps(item[c], default=str) if c == "details" else item[c] for c in COLUMNS] + [item["row_hash"]])
    return Response(buffer.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="audit-{stamp}.csv"'})
