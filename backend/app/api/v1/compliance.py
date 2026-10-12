from datetime import datetime
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.database import get_db
from app.models.compliance import ComplianceAssessment
from app.services import compliance as cz
from app.logging_config import get_logger

logger = get_logger(__name__)
router = APIRouter()


class AssessmentRequest(BaseModel):
    framework: str = "all"


def _overall(results: Optional[dict]) -> Optional[dict]:
    if not results:
        return None
    return {fw_id: s["score"] for fw_id, s in results.get("summary", {}).items()}


@router.get("/frameworks")
async def list_frameworks():
    return {
        "frameworks": [
            {
                "id": fw_id,
                "name": fw["name"],
                "requirements": len(fw["requirements"]),
            }
            for fw_id, fw in cz.FRAMEWORKS.items()
        ],
        "checks": [{"id": cid, "title": title} for cid, (title, _) in cz.CHECKS.items()],
    }


@router.get("/mappings")
async def get_mappings():
    """Each framework requirement with the checks that evidence it and any review note. For auditor review."""
    return cz.mapping_catalogue()


@router.post("/assessments", status_code=202)
async def start_assessment(request: AssessmentRequest, db: AsyncSession = Depends(get_db)):
    from app.tasks import run_compliance_assessment

    if request.framework != "all" and request.framework not in cz.FRAMEWORKS:
        raise HTTPException(status_code=400, detail=f"Unknown framework: {request.framework}")

    row = ComplianceAssessment(framework=request.framework, status="running")
    db.add(row)
    await db.commit()
    await db.refresh(row)

    task = run_compliance_assessment.delay(str(row.id))
    logger.info("compliance_assessment_queued", assessment_id=str(row.id), task_id=task.id)
    return {"id": str(row.id), "status": row.status, "framework": row.framework}


@router.get("/assessments")
async def list_assessments(limit: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ComplianceAssessment).order_by(ComplianceAssessment.created_at.desc()).limit(limit)
    )
    return {
        "assessments": [
            {
                "id": str(a.id),
                "framework": a.framework,
                "status": a.status,
                "created_at": a.created_at.isoformat(),
                "completed_at": a.completed_at.isoformat() if a.completed_at else None,
                "overall": _overall(a.results),
                "assessed_hosts": (a.results or {}).get("assessed_hosts"),
                "total_hosts": (a.results or {}).get("total_hosts"),
                "error": a.error,
            }
            for a in result.scalars().all()
        ]
    }


async def _get_assessment(db: AsyncSession, assessment_id: str) -> ComplianceAssessment:
    try:
        parsed = uuid.UUID(assessment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Assessment not found")
    row = (await db.execute(select(ComplianceAssessment).where(ComplianceAssessment.id == parsed))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Assessment not found")
    return row


@router.get("/assessments/{assessment_id}")
async def get_assessment(assessment_id: str, db: AsyncSession = Depends(get_db)):
    row = await _get_assessment(db, assessment_id)
    return {
        "id": str(row.id),
        "framework": row.framework,
        "status": row.status,
        "error": row.error,
        "created_at": row.created_at.isoformat(),
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "results": row.results,
    }


@router.get("/assessments/{assessment_id}/download")
async def download_assessment(
    assessment_id: str,
    format: str = Query("pdf", pattern="^(pdf|csv)$"),
    framework: str = Query("all"),
    db: AsyncSession = Depends(get_db),
):
    row = await _get_assessment(db, assessment_id)
    if row.status != "completed" or not row.results:
        raise HTTPException(status_code=409, detail=f"Assessment is {row.status}; download is available when completed")
    if framework != "all" and framework not in cz.FRAMEWORKS:
        raise HTTPException(status_code=400, detail=f"Unknown framework: {framework}")

    stamp = row.created_at.strftime("%Y%m%d-%H%M")
    filename = f"compliance-{framework}-{stamp}.{format}"
    from app.services import audit as _audit
    await _audit.record('compliance.report.download', category='compliance', resource_type='assessment', resource_id=assessment_id,
                        details={'format': format, 'framework': framework})
    if format == "csv":
        body = cz.build_csv(row.results, framework)
        media = "text/csv"
    else:
        body = cz.build_pdf(cz.build_report_lines(row.results, framework))
        media = "application/pdf"
    return Response(content=body, media_type=media,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
