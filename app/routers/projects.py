"""
SolarSync - Projects router: Internal operations tracker and public customer status link.
Provides milestone tracking, stuck project detection, document management, and mock WhatsApp/SMS alerts.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.models.quote import Quote
from app.models.project import (
    Project,
    ProjectStage,
    STAGE_METADATA,
    STAGE_ORDER,
)
from app.services.quote_service import get_quote_by_number
from app.services.project_service import (
    get_project_by_token,
    get_project_by_identifier,
    list_projects,
    create_project_for_lead,
    advance_project_stage,
    upload_project_document,
)
from app.utils.dependencies import require_roles

router = APIRouter(tags=["Projects"])
templates = Jinja2Templates(directory="app/templates")


# -------------------------------------------------------------------------
# Public Customer Status Link (NO LOGIN REQUIRED)
# -------------------------------------------------------------------------
@router.get("/track/{token}", response_class=HTMLResponse)
async def public_customer_tracking(
    request: Request,
    token: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Public customer project status page accessed via token link.
    No login required — accessible directly from SMS/WhatsApp notifications.
    """
    project = await get_project_by_token(db, token)
    if not project:
        raise HTTPException(status_code=404, detail="Project tracking link invalid or expired")

    # Build sequential milestone stage list with status (completed, active, upcoming)
    current_idx = STAGE_METADATA[project.stage]["index"]
    milestones = []
    for stg in STAGE_ORDER:
        meta = STAGE_METADATA[stg]
        idx = meta["index"]
        if idx < current_idx:
            step_status = "completed"
        elif idx == current_idx:
            step_status = "active"
        else:
            step_status = "upcoming"

        milestones.append({
            "stage": stg,
            "meta": meta,
            "status": step_status,
        })

    return templates.TemplateResponse(
        request=request,
        name="pages/customer_status.html",
        context={
            "title": f"Project Status · {project.project_id} — SolarSync",
            "project": project,
            "lead": project.lead,
            "quote": project.quote,
            "milestones": milestones,
            "current_meta": STAGE_METADATA[project.stage],
            "documents": project.documents,
            "notifications": project.notifications,
            "stage_metadata": STAGE_METADATA,
        },
    )


# -------------------------------------------------------------------------
# Staff & Admin: Projects Pipeline & Stuck Project Dashboard
# -------------------------------------------------------------------------
@router.get("/projects", response_class=HTMLResponse)
async def projects_pipeline_dashboard(
    request: Request,
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Staff dashboard showing all projects with stuck project alert badges."""
    all_projects = await list_projects(db, limit=200)

    # Compute key operational metrics
    total_projects = len(all_projects)
    stuck_projects = [p for p in all_projects if p.is_stuck]
    active_projects = [p for p in all_projects if p.stage != ProjectStage.SUBSIDY_CREDITED]
    completed_projects = [p for p in all_projects if p.stage == ProjectStage.SUBSIDY_CREDITED]
    net_meter_pending = [p for p in all_projects if p.stage == ProjectStage.NET_METERING]

    return templates.TemplateResponse(
        request=request,
        name="pages/projects_list.html",
        context={
            "title": "Projects Pipeline & Tracker — SolarSync",
            "user": current_user,
            "projects": all_projects,
            "stuck_projects": stuck_projects,
            "total_count": total_projects,
            "active_count": len(active_projects),
            "stuck_count": len(stuck_projects),
            "completed_count": len(completed_projects),
            "net_meter_count": len(net_meter_pending),
            "stage_metadata": STAGE_METADATA,
        },
    )


# -------------------------------------------------------------------------
# Staff & Admin: Internal Project Detail & Stage Management
# -------------------------------------------------------------------------
@router.get("/projects/{project_identifier}", response_class=HTMLResponse)
async def project_detail_view(
    request: Request,
    project_identifier: str,
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Internal operational view of an installation project."""
    project = await get_project_by_identifier(db, project_identifier)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    current_idx = STAGE_METADATA[project.stage]["index"]
    milestones = []
    for stg in STAGE_ORDER:
        meta = STAGE_METADATA[stg]
        idx = meta["index"]
        step_status = "completed" if idx < current_idx else ("active" if idx == current_idx else "upcoming")
        milestones.append({
            "stage": stg,
            "meta": meta,
            "status": step_status,
        })

    # Determine next stage if any
    next_stage = None
    if current_idx < len(STAGE_ORDER):
        next_stage = STAGE_ORDER[current_idx]

    return templates.TemplateResponse(
        request=request,
        name="pages/project_detail.html",
        context={
            "title": f"Manage {project.project_id} — SolarSync Operations",
            "user": current_user,
            "project": project,
            "lead": project.lead,
            "quote": project.quote,
            "milestones": milestones,
            "current_meta": STAGE_METADATA[project.stage],
            "next_stage": next_stage,
            "next_stage_meta": STAGE_METADATA[next_stage] if next_stage else None,
            "stage_order": STAGE_ORDER,
            "stage_metadata": STAGE_METADATA,
        },
    )


# -------------------------------------------------------------------------
# Stage Progression Endpoint
# -------------------------------------------------------------------------
@router.post("/projects/{project_identifier}/advance")
async def advance_stage_endpoint(
    project_identifier: str,
    new_stage: ProjectStage = Form(...),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Advance milestone and dispatch mock WhatsApp/SMS notifications."""
    project = await get_project_by_identifier(db, project_identifier)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await advance_project_stage(
        db=db,
        project=project,
        new_stage=new_stage,
        user_id=current_user.id,
        notes=notes,
    )

    return RedirectResponse(
        url=f"/projects/{project.project_id}?success=stage_advanced",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# -------------------------------------------------------------------------
# Document Upload Endpoint
# -------------------------------------------------------------------------
@router.post("/projects/{project_identifier}/documents")
async def upload_document_endpoint(
    project_identifier: str,
    file: UploadFile = File(...),
    document_type: str = Form(...),
    title: str = Form(...),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Attach engineering, DISCOM, or subsidy document to the project."""
    project = await get_project_by_identifier(db, project_identifier)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    await upload_project_document(
        db=db,
        project=project,
        file=file,
        document_type=document_type,
        title=title,
        user_id=current_user.id,
    )

    return RedirectResponse(
        url=f"/projects/{project.project_id}?success=document_uploaded",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# -------------------------------------------------------------------------
# Convert Accepted Quote to Active Tracked Project
# -------------------------------------------------------------------------
@router.post("/quotes/{quote_number}/convert-project")
async def convert_quote_to_project(
    quote_number: str,
    advance_amount_rs: float = Form(25000.0),
    assigned_engineer: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Convert an accepted quotation into an active tracked project."""
    quote = await get_quote_by_number(db, quote_number)
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    project = await create_project_for_lead(
        db=db,
        lead=quote.lead,
        quote=quote,
        advance_amount_rs=advance_amount_rs,
        assigned_engineer=assigned_engineer,
        notes=notes,
        user_id=current_user.id,
    )

    return RedirectResponse(
        url=f"/projects/{project.project_id}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
