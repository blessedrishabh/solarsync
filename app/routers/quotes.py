"""
SolarSync - Quotes router: Quote Builder, Customer Quote Story, and PDF downloads.
Implements dated subsidy and payback maths, ReportLab PDF generation, and homeowner presentation.
"""

import os
from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User, UserRole
from app.models.lead import Lead, LeadStatus
from app.models.quote import Quote
from app.models.rules import SubsidyRule, PricingRule
from app.services.lead_service import get_lead_by_lead_id, get_bill_reading_by_lead_id
from app.services.quote_service import (
    get_active_subsidy_rule,
    get_active_pricing_rule,
    calculate_quote_financials,
    create_quote_for_lead,
    get_quote_by_number,
    list_quotes,
    generate_quote_pdf_file,
)
from app.services.project_service import create_project_for_lead
from app.utils.dependencies import get_current_user, require_roles
from app.services.auth_service import decode_access_token, get_user_by_id

router = APIRouter(tags=["Quotes"])
templates = Jinja2Templates(directory="app/templates")


async def get_optional_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> Optional[User]:
    """Helper to get user if logged in, but don't force redirect if anonymous."""
    token = request.cookies.get("access_token")
    if not token:
        return None
    token = token.strip('"').strip("'")
    if token.startswith("Bearer "):
        token = token[7:]
    token_data = decode_access_token(token)
    if not token_data or not token_data.user_id:
        return None
    return await get_user_by_id(db, token_data.user_id)


# -------------------------------------------------------------------------
# Sales Staff: Quotations Pipeline List
# -------------------------------------------------------------------------
@router.get("/quotes", response_class=HTMLResponse)
async def quotes_list_page(
    request: Request,
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Sales and Admin view of all generated solar quotations."""
    quotes = await list_quotes(db, limit=100)
    
    # Preload related leads to avoid lazy loading issues
    quote_items = []
    for q in quotes:
        lead = await db.get(Lead, q.lead_id)
        quote_items.append({
            "quote": q,
            "lead": lead,
        })

    return templates.TemplateResponse(
        request=request,
        name="pages/quotes_list.html",
        context={
            "title": "Solar Quotations — SolarSync",
            "user": current_user,
            "quote_items": quote_items,
        },
    )


# -------------------------------------------------------------------------
# Sales Staff: Interactive Quote Builder
# -------------------------------------------------------------------------
@router.get("/leads/{lead_identifier}/quote/new", response_class=HTMLResponse)
async def new_quote_builder(
    request: Request,
    lead_identifier: str,
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Interactive Quote Builder for Sales Staff."""
    lead = await get_lead_by_lead_id(db, lead_identifier)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    reading = await get_bill_reading_by_lead_id(db, lead.id)
    subsidy_rule = await get_active_subsidy_rule(db)
    pricing_rule = await get_active_pricing_rule(db)

    # Determine recommended system size
    recommended_kw = 3.0
    if lead.recommended_kw:
        recommended_kw = float(lead.recommended_kw)
    elif reading and reading.monthly_units:
        recommended_kw = max(1.0, min(10.0, round(reading.monthly_units / 116.0)))
    elif lead.monthly_bill_rs:
        approx_units = lead.monthly_bill_rs / 9.0
        recommended_kw = max(1.0, min(10.0, round(approx_units / 116.0)))

    # Initial calculation preview
    initial_calc = calculate_quote_financials(recommended_kw, subsidy_rule, pricing_rule)

    return templates.TemplateResponse(
        request=request,
        name="pages/quote_builder.html",
        context={
            "title": f"Build Quote for {lead.name} — SolarSync",
            "user": current_user,
            "lead": lead,
            "reading": reading,
            "subsidy_rule": subsidy_rule,
            "pricing_rule": pricing_rule,
            "recommended_kw": recommended_kw,
            "initial_calc": initial_calc,
        },
    )


@router.post("/leads/{lead_identifier}/quote/new")
async def create_quote_submission(
    lead_identifier: str,
    system_size_kw: float = Form(...),
    panel_specs: str = Form("Mono PERC 550W Tier-1 (Half-Cut)"),
    inverter_specs: str = Form("High-Efficiency On-Grid String Inverter (IP65)"),
    notes: Optional[str] = Form(None),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Process quote creation, compute financials from dated rule snapshot, generate PDF."""
    lead = await get_lead_by_lead_id(db, lead_identifier)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    quote = await create_quote_for_lead(
        db=db,
        lead=lead,
        system_size_kw=system_size_kw,
        panel_specs=panel_specs,
        inverter_specs=inverter_specs,
        notes=notes,
    )

    return RedirectResponse(
        url=f"/quotes/{quote.quote_number}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


# -------------------------------------------------------------------------
# Customer / Homeowner: The Quote Story Page
# -------------------------------------------------------------------------
@router.get("/quotes/{quote_number}", response_class=HTMLResponse)
async def view_quote_story(
    request: Request,
    quote_number: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Homeowner & Sales proposal story.
    Presents: System Size -> Cost After Subsidy -> Payback, followed by details.
    Accessible publicly or via logged in session.
    """
    user = await get_optional_current_user(request, db)

    # Fetch quote with lead and rules
    result = await db.execute(
        select(Quote)
        .options(
            selectinload(Quote.lead),
            selectinload(Quote.subsidy_rule),
            selectinload(Quote.pricing_rule),
        )
        .where(Quote.quote_number == quote_number)
    )
    quote = result.scalar_one_or_none()

    if not quote:
        raise HTTPException(status_code=404, detail="Quotation not found")

    # Monthly breakdown for energy generation story
    monthly_gen = quote.yearly_generation_kwh // 12
    monthly_savings = quote.yearly_savings_rs / 12.0

    # Check for active project
    from app.models.project import Project
    res_proj = await db.execute(select(Project).where(Project.lead_id == quote.lead_id))
    project = res_proj.scalars().first()

    return templates.TemplateResponse(
        request=request,
        name="pages/quote_detail.html",
        context={
            "title": f"Solar Proposal {quote.quote_number} — {quote.system_size_kw} kW",
            "user": user,
            "quote": quote,
            "lead": quote.lead,
            "project": project,
            "subsidy_rule": quote.subsidy_rule,
            "pricing_rule": quote.pricing_rule,
            "monthly_gen": monthly_gen,
            "monthly_savings": monthly_savings,
        },
    )


# -------------------------------------------------------------------------
# PDF Download Endpoint
# -------------------------------------------------------------------------
@router.get("/quotes/{quote_number}/pdf")
async def download_quote_pdf(
    quote_number: str,
    db: AsyncSession = Depends(get_db),
):
    """Serve or stream the generated PDF quotation."""
    result = await db.execute(
        select(Quote)
        .options(
            selectinload(Quote.lead),
            selectinload(Quote.subsidy_rule),
            selectinload(Quote.pricing_rule),
        )
        .where(Quote.quote_number == quote_number)
    )
    quote = result.scalar_one_or_none()
    if not quote:
        raise HTTPException(status_code=404, detail="Quotation not found")

    # Verify or regenerate PDF if file not found on disk
    if not quote.pdf_path or not os.path.exists(quote.pdf_path):
        rel_path = generate_quote_pdf_file(
            quote=quote,
            lead=quote.lead,
            subsidy_rule=quote.subsidy_rule,
            pricing_rule=quote.pricing_rule,
        )
        quote.pdf_path = rel_path
        await db.commit()

    return FileResponse(
        path=quote.pdf_path,
        media_type="application/pdf",
        filename=f"SolarSync_Proposal_{quote.quote_number}.pdf",
    )


# -------------------------------------------------------------------------
# Customer Proposal Acceptance (Bridges to Project Tracker)
# -------------------------------------------------------------------------
@router.post("/quotes/{quote_number}/accept")
async def accept_quote_proposal(
    quote_number: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Customer accepts quotation, automatically initiating project tracking & customer status link."""
    quote = await get_quote_by_number(db, quote_number)
    if not quote:
        raise HTTPException(status_code=404, detail="Quote not found")

    quote.status = "accepted"
    lead = await db.get(Lead, quote.lead_id)
    user = await get_optional_current_user(request, db)

    project = await create_project_for_lead(
        db=db,
        lead=lead,
        quote=quote,
        advance_amount_rs=25000.0,
        user_id=user.id if user else None,
    )

    return RedirectResponse(
        url=f"/track/{project.public_token}?accepted=true",
        status_code=status.HTTP_303_SEE_OTHER,
    )
