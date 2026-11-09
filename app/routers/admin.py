"""
SolarSync - Admin router for managing dated subsidy schemes, turnkey pricing, and solar generation rules.
Restricted to ADMIN role.
"""

from datetime import date, datetime
from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.models.rules import SubsidyRule, PricingRule
from app.services.quote_service import (
    get_active_subsidy_rule,
    get_active_pricing_rule,
    calculate_quote_financials,
)
from app.utils.dependencies import require_roles

router = APIRouter(prefix="/admin", tags=["Admin Rules"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/rules", response_class=HTMLResponse)
async def admin_rules_page(
    request: Request,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Admin dashboard to view and manage dated subsidy and pricing rules."""
    # Fetch all subsidy rules
    subsidy_result = await db.execute(
        select(SubsidyRule).order_by(desc(SubsidyRule.effective_from))
    )
    subsidy_rules = subsidy_result.scalars().all()

    # Fetch all pricing rules
    pricing_result = await db.execute(
        select(PricingRule).order_by(desc(PricingRule.effective_from))
    )
    pricing_rules = pricing_result.scalars().all()

    # Get active rules for current date
    active_subsidy = await get_active_subsidy_rule(db)
    active_pricing = await get_active_pricing_rule(db)

    # Generate simulation data for 1kW, 2kW, 3kW, 4kW, 5kW
    simulations = []
    if active_subsidy and active_pricing:
        for kw in [1.0, 2.0, 3.0, 4.0, 5.0]:
            fin = calculate_quote_financials(kw, active_subsidy, active_pricing)
            simulations.append(fin)

    return templates.TemplateResponse(
        request=request,
        name="pages/admin_rules.html",
        context={
            "title": "Dated Rule Management — SolarSync Admin",
            "user": current_user,
            "subsidy_rules": subsidy_rules,
            "pricing_rules": pricing_rules,
            "active_subsidy": active_subsidy,
            "active_pricing": active_pricing,
            "simulations": simulations,
        },
    )


@router.post("/rules/subsidy")
async def create_subsidy_rule(
    scheme_name: str = Form(...),
    effective_from: str = Form(...),
    effective_to: Optional[str] = Form(None),
    rate_per_kw_upto_2: float = Form(...),
    rate_for_3rd_kw: float = Form(...),
    rate_above_3rd_kw: float = Form(0.0),
    cap_amount: float = Form(...),
    is_active: bool = Form(True),
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Add a new dated central or state subsidy rule."""
    from_date = datetime.strptime(effective_from, "%Y-%m-%d").date()
    to_date = datetime.strptime(effective_to, "%Y-%m-%d").date() if effective_to else None

    # If setting to active, can deactivate past conflicting rules if desired
    new_rule = SubsidyRule(
        scheme_name=scheme_name.strip(),
        effective_from=from_date,
        effective_to=to_date,
        rate_per_kw_upto_2=rate_per_kw_upto_2,
        rate_for_3rd_kw=rate_for_3rd_kw,
        rate_above_3rd_kw=rate_above_3rd_kw,
        cap_amount=cap_amount,
        is_active=is_active,
    )
    db.add(new_rule)
    await db.commit()

    return RedirectResponse(url="/admin/rules?success=subsidy_created", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/rules/pricing")
async def create_pricing_rule(
    name: str = Form(...),
    effective_from: str = Form(...),
    effective_to: Optional[str] = Form(None),
    base_cost_per_kw: float = Form(...),
    generation_factor_kwh_per_kw: float = Form(1400.0),
    tariff_per_kwh_rs: float = Form(7.50),
    is_active: bool = Form(True),
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Add a new dated turnkey pricing and generation standard."""
    from_date = datetime.strptime(effective_from, "%Y-%m-%d").date()
    to_date = datetime.strptime(effective_to, "%Y-%m-%d").date() if effective_to else None

    new_rule = PricingRule(
        name=name.strip(),
        effective_from=from_date,
        effective_to=to_date,
        base_cost_per_kw=base_cost_per_kw,
        generation_factor_kwh_per_kw=generation_factor_kwh_per_kw,
        tariff_per_kwh_rs=tariff_per_kwh_rs,
        is_active=is_active,
    )
    db.add(new_rule)
    await db.commit()

    return RedirectResponse(url="/admin/rules?success=pricing_created", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/rules/subsidy/{rule_id}/toggle")
async def toggle_subsidy_rule(
    rule_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Toggle active flag of a subsidy rule."""
    rule = await db.get(SubsidyRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Subsidy rule not found")
    rule.is_active = not rule.is_active
    await db.commit()
    return RedirectResponse(url="/admin/rules?toggled=subsidy", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/rules/pricing/{rule_id}/toggle")
async def toggle_pricing_rule(
    rule_id: int,
    current_user: User = Depends(require_roles([UserRole.ADMIN])),
    db: AsyncSession = Depends(get_db),
):
    """Toggle active flag of a pricing rule."""
    rule = await db.get(PricingRule, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Pricing rule not found")
    rule.is_active = not rule.is_active
    await db.commit()
    return RedirectResponse(url="/admin/rules?toggled=pricing", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/rules/simulate")
async def simulate_calculation(
    system_size_kw: float,
    current_user: User = Depends(require_roles([UserRole.ADMIN, UserRole.SALES])),
    db: AsyncSession = Depends(get_db),
):
    """Interactive sandbox JSON API for validating mathematics."""
    active_subsidy = await get_active_subsidy_rule(db)
    active_pricing = await get_active_pricing_rule(db)

    if not active_subsidy or not active_pricing:
        return JSONResponse(status_code=400, content={"error": "Active rules not configured"})

    fin = calculate_quote_financials(system_size_kw, active_subsidy, active_pricing)
    return JSONResponse(content=fin)
