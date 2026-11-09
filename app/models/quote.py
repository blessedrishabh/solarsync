"""
SolarSync - Quotation model for customer solar proposals with subsidy & payback analytics.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Float, DateTime, ForeignKey, Text
)
from sqlalchemy.orm import relationship
from app.database import Base


class Quote(Base):
    """Generated quotation record with dated rule associations and generated PDF."""

    __tablename__ = "quotes"

    id = Column(Integer, primary_key=True, index=True)
    quote_number = Column(String(30), unique=True, index=True, nullable=False) # e.g. QT-30881-A

    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    lead = relationship("Lead", backref="quotes")

    # Associated dated rule snapshots
    subsidy_rule_id = Column(Integer, ForeignKey("subsidy_rules.id"), nullable=False)
    subsidy_rule = relationship("SubsidyRule")

    pricing_rule_id = Column(Integer, ForeignKey("pricing_rules.id"), nullable=False)
    pricing_rule = relationship("PricingRule")

    # System parameters
    system_size_kw = Column(Float, nullable=False)              # e.g. 3.0 kW
    panel_specs = Column(String(150), default="Mono PERC 550W Tier-1 (Half-Cut)")
    inverter_specs = Column(String(150), default="High-Efficiency On-Grid String Inverter (IP65)")

    # Mathematical financials
    gross_cost_rs = Column(Float, nullable=False)               # Total turnkey cost
    subsidy_amount_rs = Column(Float, nullable=False)           # Central residential subsidy
    net_cost_rs = Column(Float, nullable=False)                 # Gross - Subsidy
    
    # Generation & Payback
    yearly_generation_kwh = Column(Integer, nullable=False)     # Size * 1400 kWh
    yearly_savings_rs = Column(Float, nullable=False)           # Yearly kWh * Tariff
    payback_years = Column(Float, nullable=False)               # Net Cost / Yearly Savings
    lifetime_savings_25yr_rs = Column(Float, nullable=False)    # (Yearly Savings * 25) - Net Cost

    # Generated PDF asset path
    pdf_path = Column(String(500), nullable=True)

    status = Column(String(30), default="ready", nullable=False) # "ready", "sent", "accepted"
    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Quote {self.quote_number}: {self.system_size_kw}kW (Net: ₹{self.net_cost_rs:,.0f}, Payback: {self.payback_years}yr)>"
