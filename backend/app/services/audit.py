"""
The audit trail. Every recorded event goes through record(), so every event has the same fields.

Events are kept in insertion order, and each one carries a hash of the event before it. Changing or deleting an
event breaks the chain at that point, which verify_chain() reports. Recording never raises: if the audit write
fails, the work continues and a warning is logged.
"""

import contextvars
import hashlib
import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import func, insert, select, text

from app.database import AsyncSessionLocal
from app.logging_config import get_logger
from app.models.audit import AuditLog

logger = get_logger(__name__)

CHAIN_LOCK = 7001
SECRET_WORDS = ("password", "secret", "token", "private", "key", "webhook", "pem", "credential", "code", "recovery")
MAX_TEXT = 2000
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# Context for events raised inside background work (jobs, scheduled runs). Set by the task that owns the work.
correlation_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("audit_correlation", default=None)
actor_var: contextvars.ContextVar[Optional[Dict[str, Any]]] = contextvars.ContextVar("audit_actor", default=None)


def redact(value: Any) -> Any:
    """Replace secret-looking fields. Only the field name is kept, so the record shows that a value changed."""
    if isinstance(value, dict):
        return {k: ("[redacted]" if any(w in str(k).lower() for w in SECRET_WORDS) else redact(v))
                for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, str) and len(value) > MAX_TEXT:
        return value[:MAX_TEXT] + f"… [{len(value)} characters]"
    return value


def diff(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Fields that changed, with the old and new values. Secret fields show only that they changed."""
    changes = {}
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old != new:
            if any(w in key.lower() for w in SECRET_WORDS):
                changes[key] = {"changed": True}
            else:
                changes[key] = {"from": redact(old), "to": redact(new)}
    return changes


def _canonical(fields: Dict[str, Any]) -> str:
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: Optional[str], fields: Dict[str, Any]) -> str:
    return hashlib.sha256(((prev_hash or "") + _canonical(fields)).encode("utf-8")).hexdigest()


def _chain_fields(seq: int, created_at: datetime, row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "seq": seq,
        "created_at": created_at.isoformat(),
        "category": row.get("category"),
        "action": row.get("action"),
        "outcome": row.get("outcome"),
        "source": row.get("source"),
        "correlation_id": row.get("correlation_id"),
        "actor_id": str(row["actor_id"]) if row.get("actor_id") else None,
        "actor_name": row.get("actor_name"),
        "resource_type": row.get("resource_type"),
        "resource_id": str(row["resource_id"]) if row.get("resource_id") else None,
        "ip_address": str(row["ip_address"]) if row.get("ip_address") else None,
        "reason": row.get("reason"),
        "details": row.get("details"),
    }


async def record(action: str, *, category: str = "system", outcome: str = "success", source: Optional[str] = None,
                 actor_id: Optional[uuid.UUID] = None, actor_name: Optional[str] = None,
                 resource_type: Optional[str] = None, resource_id: Optional[str] = None,
                 reason: Optional[str] = None, details: Optional[Dict[str, Any]] = None,
                 correlation_id: Optional[str] = None, ip_address: Optional[str] = None,
                 user_agent: Optional[str] = None) -> bool:
    """Write one audit event. Returns False if it could not be written; never raises."""
    try:
        actor = actor_var.get() or {}
        row = {
            "category": category,
            "action": action[:100],
            "outcome": outcome,
            "source": source or ("worker" if correlation_var.get() else "system"),
            "correlation_id": correlation_id or correlation_var.get(),
            "actor_id": actor_id or (uuid.UUID(actor["id"]) if actor.get("id") else None),
            "actor_name": actor_name or actor.get("name"),
            "resource_type": resource_type,
            "resource_id": uuid.UUID(resource_id) if resource_id and UUID_RE.fullmatch(resource_id) else None,
            "ip_address": ip_address,
            "reason": reason[:MAX_TEXT] if reason else None,
            "details": redact(details) if details else None,
        }
        created_at = datetime.utcnow()
        async with AsyncSessionLocal() as db:
            # Serialise writers so each event links to the one before it
            await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": CHAIN_LOCK})
            prev = (await db.execute(
                select(AuditLog.row_hash).where(AuditLog.row_hash.isnot(None)).order_by(AuditLog.seq.desc()).limit(1)
            )).scalar_one_or_none()
            seq = (await db.execute(text("SELECT nextval('audit_logs_seq_seq')"))).scalar_one()
            fields = _chain_fields(seq, created_at, row)
            row_hash = _hash(prev, fields)
            await db.execute(insert(AuditLog).values(
                seq=seq, created_at=created_at, prev_hash=prev, row_hash=row_hash,
                status="success" if outcome == "success" else "failure",
                user_agent=(user_agent or None) and user_agent[:500],
                **row,
            ))
            await db.commit()
        return True
    except Exception as e:
        logger.warning("audit_write_failed", action=action, error=str(e))
        return False


async def verify_chain() -> Dict[str, Any]:
    """Re-check every hashed event in order. Reports the first event that does not match."""
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(
            select(AuditLog).where(AuditLog.row_hash.isnot(None)).order_by(AuditLog.seq)
        )).scalars().all()
    previous = None
    for index, r in enumerate(rows):
        fields = _chain_fields(r.seq, r.created_at, {
            "category": r.category, "action": r.action, "outcome": r.outcome, "source": r.source,
            "correlation_id": r.correlation_id, "actor_id": r.actor_id, "actor_name": r.actor_name,
            "resource_type": r.resource_type, "resource_id": r.resource_id, "ip_address": r.ip_address,
            "reason": r.reason, "details": r.details,
        })
        if _hash(r.prev_hash, fields) != r.row_hash:
            return {"ok": False, "checked": index, "first_break": {"seq": r.seq, "action": r.action, "reason": "contents changed"}}
        if previous is not None and r.prev_hash != previous:
            return {"ok": False, "checked": index, "first_break": {"seq": r.seq, "action": r.action, "reason": "event missing before this one"}}
        previous = r.row_hash
    return {"ok": True, "checked": len(rows), "first_break": None}


def normalise_path(path: str) -> str:
    return UUID_RE.sub("{id}", path)


def category_for(path: str) -> str:
    parts = [p for p in path.split("/") if p]
    section = parts[2] if len(parts) > 2 else "api"
    return {
        "auth": "auth", "users": "users", "admin": "config", "jobs": "job", "servers": "server",
        "schedules": "schedule", "backup": "backup", "compliance": "compliance", "patch-groups": "patch-group",
        "patches": "patch", "distro-upgrades": "upgrade", "audit-logs": "audit",
    }.get(section, section)


def snapshot_fields(obj: Any, fields: List[str]) -> Dict[str, Any]:
    """Plain copy of selected fields, for before/after comparison. Values are made JSON-safe."""
    out: Dict[str, Any] = {}
    for name in fields:
        value = getattr(obj, name, None)
        out[name] = json.loads(json.dumps(value, default=str)) if value is not None else None
    return out


def copy_json(value: Any) -> Any:
    """Deep copy through JSON, so later changes to the live object do not change the snapshot."""
    return json.loads(json.dumps(value, default=str))
