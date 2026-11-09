"""
SolarSync - Leads router: public lead submission, bill upload, and lead management.
"""

from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User, UserRole
from app.models.lead import OwnershipType, RoofType, LeadScore, LeadStatus
from app.services.lead_service import (
    create_lead,
    save_bill_file,
    get_lead_by_lead_id,
    list_leads,
    get_bill_reading_by_lead_id,
    save_or_update_bill_reading,
    confirm_bill_reading,
    request_lead_bill_reupload,
    update_lead_status,
)
from app.services.ai_pipeline import process_bill_reading
from app.services.scoring_service import evaluate_lead_score, SURVEY_COST_INR
from app.utils.dependencies import get_current_user, require_roles
from app.services.auth_service import decode_access_token, get_user_by_id

router = APIRouter(prefix="/leads", tags=["Leads"])
templates = Jinja2Templates(directory="app/templates")


async def get_optional_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> Optional[User]:
    """Helper to get user if logged in, but don't redirect if anonymous."""
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


@router.get("/new", response_class=HTMLResponse)
async def new_lead_form(
    request: Request,
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """Render the public rooftop solar lead capture form."""
    return templates.TemplateResponse(
        request=request,
        name="pages/lead_form.html",
        context={
            "title": "Get Solar Quote — SolarSync",
            "user": current_user,
        },
    )


@router.post("/new")
async def submit_lead(
    request: Request,
    name: str = Form(...),
    phone: str = Form(...),
    email: Optional[str] = Form(None),
    ownership: str = Form(...),
    roof_type: str = Form(...),
    roof_area_sqft: float = Form(...),
    monthly_bill_rs: int = Form(...),
    pincode: str = Form(...),
    bill_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """Process lead submission with electricity bill upload."""
    errors = []

    # Validations
    if len(name.strip()) < 2:
        errors.append("Please enter your full name.")

    clean_phone = "".join(filter(str.isdigit, phone))
    if len(clean_phone) != 10:
        errors.append("Please enter a valid 10-digit mobile number.")

    clean_pincode = "".join(filter(str.isdigit, pincode))
    if len(clean_pincode) != 6:
        errors.append("PIN code must be 6 digits (e.g. 440010 for Nagpur).")

    if monthly_bill_rs <= 0:
        errors.append("Monthly electricity bill must be greater than zero.")

    if roof_area_sqft <= 50:
        errors.append("Roof area should be at least 50 sq ft.")

    # Validate ownership enum
    try:
        ownership_enum = OwnershipType(ownership)
    except ValueError:
        errors.append("Invalid ownership type selected.")
        ownership_enum = OwnershipType.OWNER

    # Validate roof type enum
    try:
        roof_type_enum = RoofType(roof_type)
    except ValueError:
        errors.append("Invalid roof type selected.")
        roof_type_enum = RoofType.CONCRETE

    # Validate file presence
    if not bill_file or not bill_file.filename:
        errors.append("Please upload a photo or PDF of your electricity bill.")

    if errors:
        return templates.TemplateResponse(
            request=request,
            name="pages/lead_form.html",
            context={
                "title": "Get Solar Quote — SolarSync",
                "errors": errors,
                "user": current_user,
                "form_data": {
                    "name": name,
                    "phone": phone,
                    "email": email,
                    "ownership": ownership,
                    "roof_type": roof_type,
                    "roof_area_sqft": roof_area_sqft,
                    "monthly_bill_rs": monthly_bill_rs,
                    "pincode": pincode,
                },
            },
            status_code=400,
        )

    # Save uploaded file
    file_saved, result_path = await save_bill_file(bill_file, lead_id="TMP")
    if not file_saved:
        return templates.TemplateResponse(
            request=request,
            name="pages/lead_form.html",
            context={
                "title": "Get Solar Quote — SolarSync",
                "errors": [result_path],
                "user": current_user,
                "form_data": {
                    "name": name,
                    "phone": phone,
                    "email": email,
                    "ownership": ownership,
                    "roof_type": roof_type,
                    "roof_area_sqft": roof_area_sqft,
                    "monthly_bill_rs": monthly_bill_rs,
                    "pincode": pincode,
                },
            },
            status_code=400,
        )

    # Create Lead record
    lead = await create_lead(
        db=db,
        name=name,
        phone=clean_phone,
        email=email,
        ownership=ownership_enum,
        roof_type=roof_type_enum,
        roof_area_sqft=roof_area_sqft,
        monthly_bill_rs=monthly_bill_rs,
        pincode=clean_pincode,
        bill_photo_path=result_path,
        user_id=current_user.id if current_user else None,
    )

    return RedirectResponse(url=f"/leads/success/{lead.lead_id}", status_code=303)


@router.get("/success/{lead_id}", response_class=HTMLResponse)
async def lead_success(
    request: Request,
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_current_user),
):
    """Lead submission success confirmation screen."""
    lead = await get_lead_by_lead_id(db, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    return templates.TemplateResponse(
        request=request,
        name="pages/lead_success.html",
        context={
            "title": f"Application Received — {lead.lead_id}",
            "lead": lead,
            "user": current_user,
        },
    )


@router.get("", response_class=HTMLResponse)
async def list_leads_page(
    request: Request,
    q: Optional[str] = None,
    score: Optional[str] = None,
    status_filter: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
):
    """Sales staff / Admin lead management pipeline with search, score filters, and ROI metrics."""
    score_enum = None
    if score:
        try:
            score_enum = LeadScore(score.lower())
        except ValueError:
            pass

    status_enum = None
    if status_filter:
        try:
            status_enum = LeadStatus(status_filter.lower())
        except ValueError:
            pass

    leads = await list_leads(db=db, score=score_enum, status=status_enum, search_query=q)

    # Compute operational savings
    low_score_count = sum(1 for l in leads if l.score == LeadScore.LOW)
    saved_survey_costs = low_score_count * SURVEY_COST_INR

    return templates.TemplateResponse(
        request=request,
        name="pages/leads_list.html",
        context={
            "title": "Lead Management — SolarSync",
            "leads": leads,
            "user": current_user,
            "q": q or "",
            "current_score": score,
            "current_status": status_filter,
            "saved_survey_costs": saved_survey_costs,
            "low_score_count": low_score_count,
        },
    )


@router.get("/{lead_id}", response_class=HTMLResponse)
async def lead_detail_page(
    request: Request,
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
):
    """Detailed view of a single lead with bill document viewer and transparent scoring rubric."""
    lead = await get_lead_by_lead_id(db, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    reading = await get_bill_reading_by_lead_id(db, lead.id)

    # Compute comprehensive multi-factor score breakdown
    score_breakdown = evaluate_lead_score(
        ownership=lead.ownership,
        roof_area_sqft=lead.roof_area_sqft,
        monthly_bill_rs=lead.monthly_bill_rs,
        monthly_units=reading.monthly_units if reading else None,
        sanctioned_load_kw=reading.sanctioned_load_kw if reading else None,
    )

    return templates.TemplateResponse(
        request=request,
        name="pages/lead_detail.html",
        context={
            "title": f"Lead {lead.lead_id} — SolarSync",
            "lead": lead,
            "reading": reading,
            "score_breakdown": score_breakdown,
            "user": current_user,
        },
    )


@router.post("/{lead_id}/action")
async def lead_action(
    lead_id: str,
    action: str = Form(...),
    note: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
):
    """Quick pipeline transitions: schedule survey, log phone screening, or archive."""
    lead = await get_lead_by_lead_id(db, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    if action == "schedule_survey":
        await update_lead_status(
            db, lead,
            new_status=LeadStatus.SURVEY_SCHEDULED,
            action_note=note or "Site surveyor visit booked by sales team",
        )
    elif action == "log_call":
        await update_lead_status(
            db, lead,
            new_status=lead.status if lead.status != LeadStatus.SUBMITTED else LeadStatus.BILL_REVIEWED,
            action_note=note or "Phone screening call completed with homeowner",
        )
    elif action == "archive":
        await update_lead_status(
            db, lead,
            new_status=LeadStatus.ARCHIVED,
            action_note=note or "Disqualified and archived",
        )

    return RedirectResponse(url=f"/leads/{lead.lead_id}?action_success={action}", status_code=303)


@router.get("/{lead_id}/review", response_class=HTMLResponse)
async def bill_review_page(
    request: Request,
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
):
    """Side-by-side bill image and AI extraction review screen."""
    lead = await get_lead_by_lead_id(db, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    reading = await get_bill_reading_by_lead_id(db, lead.id)

    # If no AI reading has run yet, trigger the LangGraph Gemini pipeline now
    if not reading:
        ai_res = await process_bill_reading(
            lead_id=lead.lead_id,
            file_path=lead.bill_photo_path,
            ownership=lead.ownership.value,
            roof_area_sqft=lead.roof_area_sqft,
            form_monthly_bill=lead.monthly_bill_rs,
        )
        reading = await save_or_update_bill_reading(db, lead, ai_res)

    return templates.TemplateResponse(
        request=request,
        name="pages/bill_review.html",
        context={
            "title": f"AI Bill Review — {lead.lead_id}",
            "lead": lead,
            "reading": reading,
            "user": current_user,
        },
    )


@router.post("/{lead_id}/confirm-review")
async def confirm_review(
    request: Request,
    lead_id: str,
    consumer_number: Optional[str] = Form(None),
    consumer_name: Optional[str] = Form(None),
    tariff_category: Optional[str] = Form(None),
    sanctioned_load_kw: Optional[float] = Form(None),
    monthly_units: Optional[int] = Form(None),
    bill_amount_rs: Optional[float] = Form(None),
    recommended_kw: Optional[float] = Form(None),
    score: str = Form(...),
    score_reason: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
):
    """Human-in-the-loop: Confirm and save validated bill values."""
    lead = await get_lead_by_lead_id(db, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    reading = await get_bill_reading_by_lead_id(db, lead.id)
    if not reading:
        # Fallback reading object if none exists
        reading = await save_or_update_bill_reading(db, lead, {})

    confirmed_data = {
        "consumer_number": consumer_number,
        "consumer_name": consumer_name,
        "tariff_category": tariff_category,
        "sanctioned_load_kw": sanctioned_load_kw,
        "monthly_units": monthly_units,
        "bill_amount_rs": bill_amount_rs,
        "recommended_kw": recommended_kw,
        "score": score,
        "score_reason": score_reason,
    }

    await confirm_bill_reading(db, lead, reading, confirmed_data, staff_user_id=current_user.id)
    return RedirectResponse(url=f"/leads/{lead.lead_id}?confirmed=1", status_code=303)


@router.post("/{lead_id}/reanalyze")
async def reanalyze_bill(
    lead_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
):
    """Force re-run the Gemini Vision LangGraph pipeline."""
    lead = await get_lead_by_lead_id(db, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    ai_res = await process_bill_reading(
        lead_id=lead.lead_id,
        file_path=lead.bill_photo_path,
        ownership=lead.ownership.value,
        roof_area_sqft=lead.roof_area_sqft,
        form_monthly_bill=lead.monthly_bill_rs,
    )
    await save_or_update_bill_reading(db, lead, ai_res)

    return RedirectResponse(url=f"/leads/{lead.lead_id}/review?reanalyzed=1", status_code=303)


@router.post("/{lead_id}/request-reupload")
async def request_reupload(
    lead_id: str,
    reason: str = Form(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles([UserRole.SALES, UserRole.ADMIN])),
):
    """Flag that customer's bill photo is unreadable/poor and needs re-upload."""
    lead = await get_lead_by_lead_id(db, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    reading = await get_bill_reading_by_lead_id(db, lead.id)
    await request_lead_bill_reupload(db, lead, reading, reason=reason)

    return RedirectResponse(url=f"/leads/{lead.lead_id}/review?reupload_requested=1", status_code=303)
