"""
SolarSync - BillReading model storing OCR and AI extracted electricity bill data.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from app.database import Base


class BillReading(Base):
    """Extracted data from an electricity bill via AI Vision pipeline."""

    __tablename__ = "bill_readings"

    id = Column(Integer, primary_key=True, index=True)
    lead_id = Column(Integer, ForeignKey("leads.id"), unique=True, nullable=False)
    lead = relationship("Lead", backref="bill_reading")

    # Extracted DISCOM and consumer details
    discom_name = Column(String(100), default="MSEDCL (Mahavitaran)", nullable=False)
    consumer_number = Column(String(50), nullable=True, index=True)  # Masked in UI for DPDP Act
    consumer_name = Column(String(150), nullable=True)
    tariff_category = Column(String(50), nullable=True)               # e.g. "LT-I Residential"
    sanctioned_load_kw = Column(Float, nullable=True)                 # e.g. 4.0
    monthly_units = Column(Integer, nullable=True)                    # Average or current units billed
    bill_amount_rs = Column(Float, nullable=True)
    billing_date = Column(String(50), nullable=True)

    # Document quality evaluation
    photo_quality = Column(String(30), default="good", nullable=False) # "good", "blurry", "unreadable", "invalid"
    quality_issues = Column(Text, nullable=True)
    needs_reupload = Column(Boolean, default=False, nullable=False)
    ai_confidence = Column(Float, default=0.0, nullable=False)

    # Human-in-the-loop confirmation
    is_confirmed = Column(Boolean, default=False, nullable=False)
    confirmed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    confirmed_by = relationship("User")
    confirmed_at = Column(DateTime, nullable=True)

    # Raw audit payload
    raw_ai_response = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def masked_consumer_number(self) -> str:
        """DPDP Act 2023: Return masked consumer number for privacy (e.g. 0410••••4756)."""
        if not self.consumer_number:
            return "—"
        raw = self.consumer_number.strip()
        if len(raw) <= 4:
            return raw
        return f"{raw[:4]}••••{raw[-4:]}"

    def __repr__(self):
        return f"<BillReading lead_id={self.lead_id} units={self.monthly_units} quality={self.photo_quality}>"
