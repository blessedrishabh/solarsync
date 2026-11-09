"""
SolarSync - Lead model representing rooftop solar inquiries.
"""

import enum
from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Enum, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from app.database import Base


class OwnershipType(str, enum.Enum):
    """Property ownership status."""
    OWNER = "owner"
    TENANT = "tenant"


class RoofType(str, enum.Enum):
    """Type of roof construction."""
    CONCRETE = "concrete"      # Flat concrete / RCC
    TIN_SHED = "tin_shed"      # Metal / Tin sheet
    TILED = "tiled"            # Mangalore tiles / sloped
    OTHER = "other"


class LeadStatus(str, enum.Enum):
    """Pipeline progression of a lead."""
    SUBMITTED = "submitted"                # Just submitted, pending bill reading
    BILL_REVIEWED = "bill_reviewed"        # Bill read & confirmed
    SURVEY_SCHEDULED = "survey_scheduled"  # Onsite survey scheduled
    QUOTE_GENERATED = "quote_generated"    # Quotation ready
    CONVERTED = "converted"                # Converted to project
    ARCHIVED = "archived"                  # Disqualified or lost


class LeadScore(str, enum.Enum):
    """AI Qualification score."""
    PENDING = "pending"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Lead(Base):
    """Solar installation inquiry record."""

    __tablename__ = "leads"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(String(20), unique=True, index=True, nullable=False)  # e.g., LD-30881

    # Personal info
    name = Column(String(120), nullable=False)
    phone = Column(String(15), nullable=False, index=True)
    email = Column(String(255), nullable=True)

    # Property & Roof details
    ownership = Column(Enum(OwnershipType), nullable=False, default=OwnershipType.OWNER)
    roof_type = Column(Enum(RoofType), nullable=False, default=RoofType.CONCRETE)
    roof_area_sqft = Column(Float, nullable=False)
    monthly_bill_rs = Column(Integer, nullable=False)
    pincode = Column(String(10), nullable=False)

    # Document upload
    bill_photo_path = Column(String(500), nullable=False)

    # Qualification & AI evaluation (populated in Phase 2/3)
    score = Column(Enum(LeadScore), nullable=False, default=LeadScore.PENDING)
    score_reason = Column(Text, nullable=True)
    recommended_kw = Column(Float, nullable=True)

    # Lifecycle status
    status = Column(Enum(LeadStatus), nullable=False, default=LeadStatus.SUBMITTED)

    # Optional association to registered user
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    user = relationship("User", backref="leads")

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Lead {self.lead_id}: {self.name} ({self.ownership.value}, ₹{self.monthly_bill_rs})>"
