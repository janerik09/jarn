from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import desc
from app.database import get_db
from app.models import PatchGroup, ApprovalStatus, PatchJob, Server
from app.models.job import JobStatus
from app.connectors import SSHConnector
from app.connectors.ssh import pinned_host_key
from app.logging_config import get_logger
from typing import List, Optional
from datetime import datetime
import uuid

logger = get_logger(__name__)
router = APIRouter()


class PatchGroupCreate(BaseModel):
    name: str
    description: Optional[str] = None
    patch_ids: List[str] = []
    server_ids: List[str] = []
    change_id: Optional[str] = None
    change_management_system: Optional[str] = None
    business_justification: Optional[str] = None
    risk_level: Optional[str] = None
    maintenance_window: Optional[str] = None


class PatchGroupUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    patch_ids: Optional[List[str]] = None
    server_ids: Optional[List[str]] = None
    change_id: Optional[str] = None
    change_management_system: Optional[str] = None
    business_justification: Optional[str] = None
    risk_level: Optional[str] = None
    maintenance_window: Optional[str] = None
    approval_status: Optional[str] = None
    approval_notes: Optional[str] = None
    scheduled_date: Optional[datetime] = None


class PatchGroupResponse(BaseModel):
    id: str
    name: str
    description: Optional[str]
    patch_ids: List[str]
    server_ids: List[str]
    change_id: Optional[str]
    change_management_system: Optional[str]
    approval_status: str
    business_justification: Optional[str]
    risk_level: Optional[str]
    maintenance_window: Optional[str]
    is_active: bool
    created_at: str
    scheduled_date: Optional[str]

    class Config:
        from_attributes = True


class VerifyChangeRequestRequest(BaseModel):
    change_id: str


class VerifyChangeRequestResponse(BaseModel):
    change_id: str
    status: str  # "approved", "pending", "rejected", "not_found"
    message: str


class CreateJobFromGroupRequest(BaseModel):
    patch_group_id: str
    patch_ids: List[str]  # Patches to apply to servers in group
    change_id: str
    job_name: str
    priority: str = "medium"
    dry_run: bool = False
    auto_rollback: bool = True


class AvailablePatchesResponse(BaseModel):
    patch_name: str
    servers_needing_patch: List[str]  # List of server hostnames needing this patch
    server_count: int


class PatchGroupAvailablePatchesResponse(BaseModel):
    group_id: str
    group_name: str
    total_servers: int
    available_patches: List[AvailablePatchesResponse]


@router.post("", response_model=PatchGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_patch_group(
    request: PatchGroupCreate,
    db: AsyncSession = Depends(get_db)
):
    """Create a new patch group"""
    try:
        patch_group = PatchGroup(
            name=request.name,
            description=request.description,
            patch_ids=request.patch_ids,
            server_ids=request.server_ids,
            change_id=request.change_id,
            change_management_system=request.change_management_system,
            business_justification=request.business_justification,
            risk_level=request.risk_level,
            maintenance_window=request.maintenance_window,
            created_by_id=None  # TODO: Get from auth context
        )
        db.add(patch_group)
        await db.commit()
        await db.refresh(patch_group)

        logger.info("patch_group_created", group_id=str(patch_group.id), name=patch_group.name)

        return PatchGroupResponse(
            id=str(patch_group.id),
            name=patch_group.name,
            description=patch_group.description,
            patch_ids=patch_group.patch_ids,
            server_ids=patch_group.server_ids,
            change_id=patch_group.change_id,
            change_management_system=patch_group.change_management_system,
            approval_status=patch_group.approval_status.value,
            business_justification=patch_group.business_justification,
            risk_level=patch_group.risk_level,
            maintenance_window=patch_group.maintenance_window,
            is_active=patch_group.is_active,
            created_at=patch_group.created_at.isoformat(),
            scheduled_date=patch_group.scheduled_date.isoformat() if patch_group.scheduled_date else None
        )
    except Exception as e:
        logger.error("patch_group_creation_error", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=List[PatchGroupResponse])
async def list_patch_groups(
    db: AsyncSession = Depends(get_db),
    include_inactive: bool = False
):
    """List all patch groups"""
    try:
        query = select(PatchGroup)
        if not include_inactive:
            query = query.where(PatchGroup.is_active == True)
        query = query.order_by(desc(PatchGroup.created_at))

        result = await db.execute(query)
        groups = result.scalars().all()

        return [
            PatchGroupResponse(
                id=str(g.id),
                name=g.name,
                description=g.description,
                patch_ids=g.patch_ids,
                server_ids=g.server_ids,
                change_id=g.change_id,
                change_management_system=g.change_management_system,
                approval_status=g.approval_status.value,
                business_justification=g.business_justification,
                risk_level=g.risk_level,
                maintenance_window=g.maintenance_window,
                is_active=g.is_active,
                created_at=g.created_at.isoformat(),
                scheduled_date=g.scheduled_date.isoformat() if g.scheduled_date else None
            )
            for g in groups
        ]
    except Exception as e:
        logger.error("list_patch_groups_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{group_id}", response_model=PatchGroupResponse)
async def get_patch_group(
    group_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Get a specific patch group"""
    try:
        result = await db.execute(
            select(PatchGroup).where(PatchGroup.id == group_id)
        )
        group = result.scalar_one_or_none()

        if not group:
            raise HTTPException(status_code=404, detail="Patch group not found")

        return PatchGroupResponse(
            id=str(group.id),
            name=group.name,
            description=group.description,
            patch_ids=group.patch_ids,
            server_ids=group.server_ids,
            change_id=group.change_id,
            change_management_system=group.change_management_system,
            approval_status=group.approval_status.value,
            business_justification=group.business_justification,
            risk_level=group.risk_level,
            maintenance_window=group.maintenance_window,
            is_active=group.is_active,
            created_at=group.created_at.isoformat(),
            scheduled_date=group.scheduled_date.isoformat() if group.scheduled_date else None
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_patch_group_error", group_id=group_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{group_id}", response_model=PatchGroupResponse)
async def update_patch_group(
    group_id: str,
    request: PatchGroupUpdate,
    db: AsyncSession = Depends(get_db)
):
    """Update a patch group"""
    try:
        result = await db.execute(
            select(PatchGroup).where(PatchGroup.id == group_id)
        )
        group = result.scalar_one_or_none()

        if not group:
            raise HTTPException(status_code=404, detail="Patch group not found")

        # Update fields
        if request.name:
            group.name = request.name
        if request.description is not None:
            group.description = request.description
        if request.patch_ids is not None:
            group.patch_ids = request.patch_ids
        if request.server_ids is not None:
            group.server_ids = request.server_ids
        if request.change_id is not None:
            group.change_id = request.change_id
        if request.change_management_system is not None:
            group.change_management_system = request.change_management_system
        if request.business_justification is not None:
            group.business_justification = request.business_justification
        if request.risk_level is not None:
            group.risk_level = request.risk_level
        if request.maintenance_window is not None:
            group.maintenance_window = request.maintenance_window
        if request.approval_status:
            group.approval_status = ApprovalStatus[request.approval_status]
            if request.approval_status == "approved":
                group.approved_at = datetime.utcnow()
        if request.approval_notes is not None:
            group.approval_notes = request.approval_notes
        if request.scheduled_date is not None:
            group.scheduled_date = request.scheduled_date

        await db.commit()
        await db.refresh(group)

        logger.info("patch_group_updated", group_id=group_id, name=group.name)

        return PatchGroupResponse(
            id=str(group.id),
            name=group.name,
            description=group.description,
            patch_ids=group.patch_ids,
            server_ids=group.server_ids,
            change_id=group.change_id,
            change_management_system=group.change_management_system,
            approval_status=group.approval_status.value,
            business_justification=group.business_justification,
            risk_level=group.risk_level,
            maintenance_window=group.maintenance_window,
            is_active=group.is_active,
            created_at=group.created_at.isoformat(),
            scheduled_date=group.scheduled_date.isoformat() if group.scheduled_date else None
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("update_patch_group_error", group_id=group_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_patch_group(
    group_id: str,
    db: AsyncSession = Depends(get_db)
):
    """Delete a patch group"""
    try:
        result = await db.execute(
            select(PatchGroup).where(PatchGroup.id == group_id)
        )
        group = result.scalar_one_or_none()

        if not group:
            raise HTTPException(status_code=404, detail="Patch group not found")

        group.is_active = False
        await db.commit()

        logger.info("patch_group_deleted", group_id=group_id, name=group.name)

        return None
    except HTTPException:
        raise
    except Exception as e:
        logger.error("delete_patch_group_error", group_id=group_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{group_id}/approve")
async def approve_patch_group(
    group_id: str,
    approval_notes: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Approve a patch group (mark as approved in change management)"""
    try:
        result = await db.execute(
            select(PatchGroup).where(PatchGroup.id == group_id)
        )
        group = result.scalar_one_or_none()

        if not group:
            raise HTTPException(status_code=404, detail="Patch group not found")

        group.approval_status = ApprovalStatus.approved
        group.approved_at = datetime.utcnow()
        if approval_notes:
            group.approval_notes = approval_notes

        await db.commit()

        logger.info("patch_group_approved", group_id=group_id)

        return {
            "id": str(group.id),
            "approval_status": group.approval_status.value,
            "approved_at": group.approved_at.isoformat()
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("approve_patch_group_error", group_id=group_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/verify-change-request")
async def verify_change_request(
    request: VerifyChangeRequestRequest
) -> VerifyChangeRequestResponse:
    """Verify change request approval status (placeholder - always returns approved for now)"""
    try:
        # TODO: Integrate with actual change management system (ServiceNow, Jira, etc.)
        # For now, this is a placeholder that accepts any change ID

        logger.info("change_request_verified", change_id=request.change_id)

        # Placeholder: all change requests are "approved"
        return VerifyChangeRequestResponse(
            change_id=request.change_id,
            status="approved",
            message=f"Change request {request.change_id} is approved"
        )
    except Exception as e:
        logger.error("verify_change_request_error", change_id=request.change_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{group_id}/create-job")
async def create_job_from_patch_group(
    group_id: str,
    request: CreateJobFromGroupRequest,
    db: AsyncSession = Depends(get_db)
):
    """Create a patch job from a patch group"""
    try:
        # Get the patch group
        group_result = await db.execute(
            select(PatchGroup).where(PatchGroup.id == group_id)
        )
        group = group_result.scalar_one_or_none()

        if not group:
            raise HTTPException(status_code=404, detail="Patch group not found")

        # Verify servers exist
        if not group.server_ids:
            raise HTTPException(status_code=400, detail="Patch group has no servers")

        # Create the patch job
        job = PatchJob(
            name=request.job_name,
            status=JobStatus.pending,
            patch_ids=request.patch_ids,
            server_ids=group.server_ids,
            priority=request.priority,
            dry_run=request.dry_run,
            auto_rollback=request.auto_rollback,
            change_id=request.change_id,
            change_management_system="placeholder",
            patch_group_id=group_id
        )

        db.add(job)
        await db.commit()
        await db.refresh(job)

        logger.info("job_created_from_group", job_id=str(job.id),
                   group_id=group_id, server_count=len(group.server_ids),
                   patch_count=len(request.patch_ids))

        return {
            "id": str(job.id),
            "name": job.name,
            "status": job.status.value,
            "server_ids": job.server_ids,
            "patch_ids": job.patch_ids,
            "change_id": job.change_id,
            "message": f"Job created with {len(group.server_ids)} servers and {len(request.patch_ids)} patches"
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("create_job_from_group_error", group_id=group_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{group_id}/available-patches")
async def get_group_available_patches(
    group_id: str,
    db: AsyncSession = Depends(get_db)
) -> PatchGroupAvailablePatchesResponse:
    """Get available patches for all servers in a patch group"""
    try:
        # Get the patch group
        group_result = await db.execute(
            select(PatchGroup).where(PatchGroup.id == group_id)
        )
        group = group_result.scalar_one_or_none()

        if not group:
            raise HTTPException(status_code=404, detail="Patch group not found")

        if not group.server_ids:
            return PatchGroupAvailablePatchesResponse(
                group_id=group_id,
                group_name=group.name,
                total_servers=0,
                available_patches=[]
            )

        # Get all servers in the group
        servers_result = await db.execute(
            select(Server).where(Server.id.in_(group.server_ids))
        )
        servers = servers_result.scalars().all()

        if not servers:
            return PatchGroupAvailablePatchesResponse(
                group_id=group_id,
                group_name=group.name,
                total_servers=0,
                available_patches=[]
            )

        # Scan patches from all servers and aggregate
        patches_by_name = {}

        from app.models.server import OSType

        for server in servers:
            try:
                connector = SSHConnector(
                    hostname=str(server.ip_address) if server.ip_address else server.hostname,
                    username=server.credentials.get("username") if server.credentials else None,
                    private_key=server.credentials.get("private_key") if server.credentials else None,
                    password=server.credentials.get("password") if server.credentials else None,
                    host_key=pinned_host_key(server.metadata_),
                )

                connected = await connector.connect()
                if connected:
                    patches = await connector.scan_available_patches()
                    await connector.disconnect()

                    for patch in patches:
                        if patch.name not in patches_by_name:
                            patches_by_name[patch.name] = set()
                        patches_by_name[patch.name].add(server.hostname)
            except Exception as e:
                logger.warning("patch_scan_server_error", hostname=server.hostname, error=str(e))
                continue

        available_patches = [
            AvailablePatchesResponse(
                patch_name=patch_name,
                servers_needing_patch=sorted(list(hostnames)),
                server_count=len(hostnames)
            )
            for patch_name, hostnames in patches_by_name.items()
        ]

        available_patches.sort(key=lambda x: x.server_count, reverse=True)

        logger.info("get_group_available_patches", group_id=group_id,
                   server_count=len(servers), patch_count=len(available_patches))

        return PatchGroupAvailablePatchesResponse(
            group_id=group_id,
            group_name=group.name,
            total_servers=len(servers),
            available_patches=available_patches
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("get_group_available_patches_error", group_id=group_id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
