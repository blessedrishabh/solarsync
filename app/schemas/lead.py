"""
SolarSync - Pydantic schemas for Lead validation and serialization.
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field, EmailStr
from app.models.lead import OwnershipType, RoofType, LeadStatus, LeadScore


class LeadCreateForm(BaseModel):
    """Schema for lead capture form validation."""
    name: str = Field(..., min_length=2, max_length=120)
    phone: str = Field(..., pattern=r"^[6-9]\d{9}$", description="10-digit Indian mobile number")
    email: Optional[EmailStr] = None
    ownership: OwnershipType
    roof_type: RoofType
    roof_area_sqft: float = Field(..., gt=50, lt=100000, description="Roof area in square feet")
    monthly_bill_rs: int = Field(..., gt=0, lt=1000000, description="Average monthly electricity bill in INR")
    pincode: str = Field(..., pattern=r"^\d{6}$", description="6-digit PIN code")


class LeadResponse(BaseModel):
    """Schema for lead representation in responses."""
    id: int
    lead_id: str
    name: str
    phone: str
    email: Optional[str] = None
    ownership: OwnershipType
    roof_type: RoofType
    roof_area_sqft: float
    monthly_bill_rs: int
    pincode: str
    bill_photo_path: str
    score: LeadScore
    score_reason: Optional[str] = None
    recommended_kw: Optional[float] = None
    status: LeadStatus
    created_at: datetime

    model_config = {"from_attributes": True}
