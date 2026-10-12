"""Platform-wide status that every signed-in user may read, such as whether backups are protected off-host."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services import offsite_status

router = APIRouter()


@router.get("/backup-status")
async def backup_status(db: AsyncSession = Depends(get_db)):
    result = await offsite_status.status(db)
    return {"state": result["state"], "problem": result["problem"], "message": result["message"]}
