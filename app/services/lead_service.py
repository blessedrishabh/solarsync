"""
SolarSync - Lead management and file storage service.
"""

import os
import uuid
import random
from typing import List, Optional, Tuple
from fastapi import UploadFile
from sqlalchemy import select, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.lead import (
    Lead,
    OwnershipType,
    RoofType,
    LeadStatus,
    LeadScore,
)
from app.models.bill_reading import BillReading
from datetime import datetime

settings = get_settings()

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "application/pdf",
}

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".pdf"}


async def generate_lead_id(db: AsyncSession) -> str:
    """Generate a unique human-friendly Lead ID like LD-30881."""
    while True:
        num = random.randint(30000, 99999)
        lead_id = f"LD-{num}"
        result = await db.execute(select(Lead).where(Lead.lead_id == lead_id))
        if not result.scalar_one_or_none():
            return lead_id


async def save_bill_file(file: UploadFile, lead_id: str) -> Tuple[bool, str]:
    """
    Validate and save uploaded electricity bill file.
    Returns (success: bool, path_or_error: str).
    """
    if not file or not file.filename:
        return False, "Please upload your electricity bill."

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return False, f"Unsupported file type '{ext}'. Please upload JPG, PNG, WEBP, or PDF."

    # Ensure uploads directory exists
    bill_dir = os.path.join(settings.UPLOAD_DIR, "bills")
    os.makedirs(bill_dir, exist_ok=True)

    # Secure unique filename
    unique_suffix = uuid.uuid4().hex[:8]
    safe_filename = f"{lead_id}_{unique_suffix}{ext}"
    dest_path = os.path.join(bill_dir, safe_filename)

    # Read and check size
    contents = await file.read()
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        return False, f"File size exceeds the {settings.MAX_UPLOAD_SIZE_MB}MB limit."

    with open(dest_path, "wb") as f:
        f.write(contents)

    # Return relative forward-slash path for portability
    rel_path = f"uploads/bills/{safe_filename}"
    return True, rel_path


async def create_lead(
    db: AsyncSession,
    name: str,
    phone: str,
    ownership: OwnershipType,
    roof_type: RoofType,
    roof_area_sqft: float,
    monthly_bill_rs: int,
    pincode: str,
    bill_photo_path: str,
    email: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Lead:
    """Create a new Lead in the database."""
    lead_id = await generate_lead_id(db)

    # Basic baseline qualification rule (full AI analysis comes in Phase 2/3):
    # If tenant -> immediately flagged as Low score (tenant)
    # If bill < 800 -> flagged as Low score (small bill)
    # If owner and bill >= 2500 -> High score
    # Otherwise Medium
    initial_score = LeadScore.PENDING
    score_reason = "Pending AI bill review"
    recommended_kw = None

    if ownership == OwnershipType.TENANT:
        initial_score = LeadScore.LOW
        score_reason = "Property is rented / tenant"
    elif monthly_bill_rs < 800:
        initial_score = LeadScore.LOW
        score_reason = "Monthly bill under ₹800 (low solar savings)"
        recommended_kw = 1.0
    elif monthly_bill_rs >= 2500:
        initial_score = LeadScore.HIGH
        score_reason = "High consumption + homeowner"
        # Rough estimation: ₹1,000 bill ~ 1 kW
        recommended_kw = round(min(monthly_bill_rs / 1000.0, roof_area_sqft / 100.0), 1)
    else:
        initial_score = LeadScore.MEDIUM
        score_reason = "Moderate consumption"
        recommended_kw = round(min(monthly_bill_rs / 1000.0, roof_area_sqft / 100.0), 1)

    lead = Lead(
        lead_id=lead_id,
        name=name.strip(),
        phone=phone.strip(),
        email=email.strip() if email else None,
        ownership=ownership,
        roof_type=roof_type,
        roof_area_sqft=roof_area_sqft,
        monthly_bill_rs=monthly_bill_rs,
        pincode=pincode.strip(),
        bill_photo_path=bill_photo_path,
        score=initial_score,
        score_reason=score_reason,
        recommended_kw=recommended_kw,
        status=LeadStatus.SUBMITTED,
        user_id=user_id,
    )
    db.add(lead)
    await db.flush()
    await db.refresh(lead)
    return lead


async def get_lead_by_lead_id(db: AsyncSession, lead_id: str) -> Optional[Lead]:
    """Fetch lead by its formatted public lead_id (e.g. LD-30881)."""
    result = await db.execute(select(Lead).where(Lead.lead_id == lead_id))
    return result.scalar_one_or_none()


async def get_lead_by_id(db: AsyncSession, lead_db_id: int) -> Optional[Lead]:
    """Fetch lead by internal primary key."""
    result = await db.execute(select(Lead).where(Lead.id == lead_db_id))
    return result.scalar_one_or_none()


async def list_leads(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 50,
    score: Optional[LeadScore] = None,
    status: Optional[LeadStatus] = None,
    search_query: Optional[str] = None,
) -> List[Lead]:
    """List leads with optional filtering and search, ordered newest first."""
    query = select(Lead).order_by(desc(Lead.created_at))
    if score:
        query = query.where(Lead.score == score)
    if status:
        query = query.where(Lead.status == status)
    if search_query and search_query.strip():
        term = f"%{search_query.strip()}%"
        query = query.where(
            or_(
                Lead.name.ilike(term),
                Lead.phone.ilike(term),
                Lead.lead_id.ilike(term),
                Lead.pincode.ilike(term),
            )
        )
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def update_lead_status(
    db: AsyncSession,
    lead: Lead,
    new_status: LeadStatus,
    action_note: Optional[str] = None,
) -> Lead:
    """Transition lead to a new pipeline status (e.g. SURVEY_SCHEDULED)."""
    lead.status = new_status
    if action_note:
        if lead.score_reason:
            lead.score_reason = f"{lead.score_reason} | {action_note}"
        else:
            lead.score_reason = action_note
    await db.commit()
    await db.refresh(lead)
    return lead


async def list_leads_for_user(db: AsyncSession, user_id: int) -> List[Lead]:
    """List all leads submitted by a specific user."""
    query = select(Lead).where(Lead.user_id == user_id).order_by(desc(Lead.created_at))
    result = await db.execute(query)
    return list(result.scalars().all())


async def seed_sample_leads(db: AsyncSession):
    """Seed the realistic sample data provided in the brief if not present."""
    sample_leads = [
        {
            "lead_id": "LD-30881",
            "name": "Prashant Wankhede",
            "phone": "9876543210",
            "email": "prashant@example.com",
            "ownership": OwnershipType.OWNER,
            "roof_type": RoofType.CONCRETE,
            "roof_area_sqft": 450.0,
            "monthly_bill_rs": 3200,
            "pincode": "440010",
            "bill_photo_path": "uploads/bills/sample_bill_30881.jpg",
            "score": LeadScore.HIGH,
            "score_reason": "High bill (340 units avg) + owner with clear concrete roof",
            "recommended_kw": 3.0,
            "status": LeadStatus.SUBMITTED,
        },
        {
            "lead_id": "LD-30902",
            "name": "Rohit Meshram",
            "phone": "9823012345",
            "email": "rohit.m@example.com",
            "ownership": OwnershipType.TENANT,
            "roof_type": RoofType.CONCRETE,
            "roof_area_sqft": 300.0,
            "monthly_bill_rs": 1900,
            "pincode": "440022",
            "bill_photo_path": "uploads/bills/sample_bill_30902.jpg",
            "score": LeadScore.LOW,
            "score_reason": "Low (tenant) - cannot install without landlord consent",
            "recommended_kw": None,
            "status": LeadStatus.SUBMITTED,
        },
        {
            "lead_id": "LD-30915",
            "name": "Ayesha Siddiqui",
            "phone": "9765432109",
            "email": "ayesha.s@example.com",
            "ownership": OwnershipType.OWNER,
            "roof_type": RoofType.TIN_SHED,
            "roof_area_sqft": 200.0,
            "monthly_bill_rs": 650,
            "pincode": "440015",
            "bill_photo_path": "uploads/bills/sample_bill_30915.jpg",
            "score": LeadScore.LOW,
            "score_reason": "Low (small bill) - 85 units avg, long payback period",
            "recommended_kw": 1.0,
            "status": LeadStatus.SUBMITTED,
        },
    ]

    for item in sample_leads:
        existing = await get_lead_by_lead_id(db, item["lead_id"])
        if not existing:
            lead = Lead(**item)
            db.add(lead)

    # Create dummy sample bill file placeholder if needed
    os.makedirs(os.path.join(settings.UPLOAD_DIR, "bills"), exist_ok=True)
    sample_img_path = os.path.join(settings.UPLOAD_DIR, "bills", "sample_bill_30881.jpg")
    if not os.path.exists(sample_img_path):
        with open(sample_img_path, "wb") as f:
            f.write(b"SAMPLE BILL PLACEHOLDER IMAGE")

    await db.commit()


async def get_bill_reading_by_lead_id(db: AsyncSession, lead_db_id: int) -> Optional[BillReading]:
    """Fetch the BillReading associated with a lead."""
    result = await db.execute(select(BillReading).where(BillReading.lead_id == lead_db_id))
    return result.scalar_one_or_none()


async def save_or_update_bill_reading(
    db: AsyncSession,
    lead: Lead,
    ai_result: dict,
) -> BillReading:
    """Save or update AI-extracted bill reading from LangGraph pipeline output."""
    reading = await get_bill_reading_by_lead_id(db, lead.id)

    if not reading:
        reading = BillReading(lead_id=lead.id)
        db.add(reading)

    reading.discom_name = ai_result.get("discom_name") or "MSEDCL (Mahavitaran)"
    reading.consumer_number = ai_result.get("consumer_number")
    reading.consumer_name = ai_result.get("consumer_name")
    reading.tariff_category = ai_result.get("tariff_category")
    reading.sanctioned_load_kw = ai_result.get("sanctioned_load_kw")
    reading.monthly_units = ai_result.get("monthly_units")
    reading.bill_amount_rs = ai_result.get("bill_amount_rs")
    reading.photo_quality = ai_result.get("photo_quality", "good")
    reading.quality_issues = ai_result.get("quality_issues")
    reading.needs_reupload = ai_result.get("needs_reupload", False)
    reading.ai_confidence = ai_result.get("ai_confidence", 0.0)
    reading.raw_ai_response = ai_result.get("raw_response")

    # Update lead score and recommended sizing based on AI analysis
    lead_score_str = ai_result.get("lead_score", "pending")
    try:
        lead.score = LeadScore(lead_score_str)
    except ValueError:
        pass

    if ai_result.get("score_reason"):
        lead.score_reason = ai_result["score_reason"]

    if ai_result.get("recommended_kw") is not None:
        lead.recommended_kw = ai_result["recommended_kw"]

    await db.flush()
    await db.refresh(reading)
    await db.refresh(lead)
    return reading


async def confirm_bill_reading(
    db: AsyncSession,
    lead: Lead,
    reading: BillReading,
    confirmed_data: dict,
    staff_user_id: int,
) -> Tuple[Lead, BillReading]:
    """Human-in-the-loop: Sales staff confirms or edits extracted bill data side-by-side."""
    reading.consumer_number = confirmed_data.get("consumer_number") or reading.consumer_number
    reading.consumer_name = confirmed_data.get("consumer_name") or reading.consumer_name
    reading.tariff_category = confirmed_data.get("tariff_category") or reading.tariff_category
    reading.sanctioned_load_kw = confirmed_data.get("sanctioned_load_kw")
    reading.monthly_units = confirmed_data.get("monthly_units")
    reading.bill_amount_rs = confirmed_data.get("bill_amount_rs")
    reading.is_confirmed = True
    reading.confirmed_by_id = staff_user_id
    reading.confirmed_at = datetime.utcnow()
    reading.needs_reupload = False

    # Update lead status
    lead.status = LeadStatus.BILL_REVIEWED
    if confirmed_data.get("score"):
        try:
            lead.score = LeadScore(confirmed_data["score"])
        except ValueError:
            pass
    if confirmed_data.get("recommended_kw") is not None:
        lead.recommended_kw = confirmed_data["recommended_kw"]
    if confirmed_data.get("score_reason"):
        lead.score_reason = confirmed_data["score_reason"]

    await db.commit()
    await db.refresh(reading)
    await db.refresh(lead)
    return lead, reading


async def request_lead_bill_reupload(
    db: AsyncSession,
    lead: Lead,
    reading: Optional[BillReading],
    reason: str,
) -> Lead:
    """Flag lead as requiring a clearer bill photo upload."""
    if reading:
        reading.needs_reupload = True
        reading.quality_issues = reason
    lead.score_reason = f"Re-upload required: {reason}"
    await db.commit()
    await db.refresh(lead)
    return lead
