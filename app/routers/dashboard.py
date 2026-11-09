"""
SolarSync - Dashboard route. Role-based landing page after login.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.user import User, UserRole
from app.models.lead import Lead, LeadScore, LeadStatus
from app.services.scoring_service import SURVEY_COST_INR
from app.services.lead_service import list_leads, list_leads_for_user
from app.utils.dependencies import get_current_user

router = APIRouter(tags=["Dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Render dynamic role-based dashboard with lead metrics and tables."""
    context = {
        "title": "Dashboard — SolarSync",
        "user": current_user,
    }

    if current_user.role in [UserRole.SALES, UserRole.ADMIN]:
        # Fetch lead statistics
        all_leads = await list_leads(db, limit=10)
        
        # Count queries
        total_res = await db.execute(select(func.count(Lead.id)))
        total_leads = total_res.scalar() or 0

        high_res = await db.execute(select(func.count(Lead.id)).where(Lead.score == LeadScore.HIGH))
        high_score_leads = high_res.scalar() or 0

        med_res = await db.execute(select(func.count(Lead.id)).where(Lead.score == LeadScore.MEDIUM))
        med_score_leads = med_res.scalar() or 0

        low_res = await db.execute(select(func.count(Lead.id)).where(Lead.score == LeadScore.LOW))
        low_score_leads = low_res.scalar() or 0

        scheduled_res = await db.execute(select(func.count(Lead.id)).where(Lead.status == LeadStatus.SURVEY_SCHEDULED))
        surveys_scheduled = scheduled_res.scalar() or 0

        # Company A Business Metric Tracking:
        # Saving ₹2,500 for each disqualified lead diverted from an on-site visit
        survey_cost_saved = low_score_leads * SURVEY_COST_INR
        unqualified_pct = round((low_score_leads / total_leads * 100), 1) if total_leads > 0 else 0.0

        context.update({
            "recent_leads": all_leads,
            "total_leads": total_leads,
            "high_score_leads": high_score_leads,
            "med_score_leads": med_score_leads,
            "low_score_leads": low_score_leads,
            "surveys_scheduled": surveys_scheduled,
            "survey_cost_saved": survey_cost_saved,
            "unqualified_pct": unqualified_pct,
        })
    else:
        # Homeowner view: fetch their own submitted leads
        user_leads = await list_leads_for_user(db, current_user.id)
        context.update({
            "user_leads": user_leads,
        })

    return templates.TemplateResponse(
        request=request,
        name="pages/dashboard.html",
        context=context,
    )
