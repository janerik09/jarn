"""
Prometheus metrics at /metrics (text exposition format). Read only; no secrets are exposed. The nginx web
container does not forward this path, so it is served only on the internal Docker network unless METRICS_TOKEN is set.
"""

import secrets
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Server, PatchJob
from app.models.compliance import ComplianceAssessment
from app.services import tls

router = APIRouter()


async def _require_metrics_token(authorization: Optional[str] = Header(None)) -> None:
    if not settings.metrics_token:
        return
    expected = f"Bearer {settings.metrics_token}"
    if not authorization or not secrets.compare_digest(authorization, expected):
        raise HTTPException(status_code=401, detail="A metrics token is required")


def _label(value) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _gauge(lines: list, name: str, help_text: str, samples: list) -> None:
    lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} gauge")
    for labels, value in samples:
        if labels:
            label_text = ",".join(f'{k}="{_label(v)}"' for k, v in labels.items())
            lines.append(f"{name}{{{label_text}}} {value}")
        else:
            lines.append(f"{name} {value}")


@router.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(_require_metrics_token)])
async def metrics(db: AsyncSession = Depends(get_db)):
    lines: list = []

    server_rows = (await db.execute(
        select(Server.connectivity_status, func.count()).group_by(Server.connectivity_status)
    )).all()
    _gauge(lines, "jarn_servers", "Servers in inventory by connectivity status",
           [({"status": s.value if hasattr(s, "value") else s}, n) for s, n in server_rows])

    pending = (await db.execute(
        select(func.coalesce(func.sum(Server.metadata_["pending_patch_count"].as_integer()), 0))
    )).scalar_one()
    _gauge(lines, "jarn_pending_patches", "Upgradable packages across the fleet, from the last scan", [({}, int(pending))])

    job_rows = (await db.execute(select(PatchJob.status, func.count()).group_by(PatchJob.status))).all()
    _gauge(lines, "jarn_jobs", "Patch jobs by status",
           [({"status": s.value if hasattr(s, "value") else s}, n) for s, n in job_rows])

    latest = (await db.execute(
        select(ComplianceAssessment)
        .where(ComplianceAssessment.status == "completed", ComplianceAssessment.framework == "all")
        .order_by(ComplianceAssessment.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    scores = []
    if latest and latest.results:
        for fw_id, summary in latest.results.get("summary", {}).items():
            if summary.get("score") is not None:
                scores.append(({"framework": fw_id}, summary["score"]))
    _gauge(lines, "jarn_compliance_score_percent", "Compliance score from the latest completed assessment", scores)

    settings_row = await tls.read_settings_for_metrics(db)
    material = tls.active_material(settings_row) if settings_row else None
    days = None
    if material:
        days = tls.summarize(material["cert_pem"])["days_left"]
    _gauge(lines, "jarn_tls_certificate_days_left", "Days until the active web certificate expires",
           [({}, days)] if days is not None else [])

    from app.services import monitoring
    state = await monitoring.snapshot()
    _gauge(lines, "jarn_worker_up", "Number of Celery workers responding", [({}, state["workers"])])
    _gauge(lines, "jarn_backup_age_seconds", "Age of the newest database backup in seconds",
           [({}, int(state["backup_age_seconds"]))] if state["backup_age_seconds"] is not None else [])
    _gauge(lines, "jarn_restore_check_age_seconds", "Seconds since the last passing backup restore check",
           [({}, int(state["restore_check_age_seconds"]))] if state["restore_check_age_seconds"] is not None else [])
    _gauge(lines, "jarn_compliance_age_seconds", "Seconds since the last completed compliance assessment",
           [({}, int(state["compliance_age_seconds"]))] if state["compliance_age_seconds"] is not None else [])
    _gauge(lines, "jarn_alert_active", "1 when a platform problem is active",
           [({"alert": k}, 1) for k in state["problems"]])

    return "\n".join(lines) + "\n"
