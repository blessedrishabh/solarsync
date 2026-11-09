"""
SolarSync - Dated subsidy and pricing rule models for rooftop solar calculations.
Rules are versioned by effective dates to support central and state policy shifts.
"""

from datetime import date, datetime
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime
)
from app.database import Base


class SubsidyRule(Base):
    """
    Dated residential solar subsidy scheme rules.
    Central Residential Scheme (PM Surya Ghar Muft Bijli Yojana):
    - ₹30,000 / kW for first 2 kW
    - ₹18,000 for 3rd kW
    - ₹0 above 3 kW (capped at ₹78,000 max)
    """

    __tablename__ = "subsidy_rules"

    id = Column(Integer, primary_key=True, index=True)
    scheme_name = Column(String(150), nullable=False)
    
    # Date range for which this rule is legally active
    effective_from = Column(Date, nullable=False, default=date(2024, 2, 15))
    effective_to = Column(Date, nullable=True)  # Null indicates indefinitely active

    # Mathematical subsidy rates per kW
    rate_per_kw_upto_2 = Column(Float, nullable=False, default=30000.0) # ₹30,000/kW
    rate_for_3rd_kw = Column(Float, nullable=False, default=18000.0)    # ₹18,000 for 3rd kW
    rate_above_3rd_kw = Column(Float, nullable=False, default=0.0)      # ₹0 above 3 kW
    cap_amount = Column(Float, nullable=False, default=78000.0)         # Max ₹78,000 cap

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def calculate_subsidy(self, system_size_kw: float) -> float:
        """
        Pure deterministic formula from dated rule:
        - 1 kW: ₹30,000
        - 2 kW: ₹60,000
        - 3 kW: ₹78,000 (60k + 18k)
        - > 3 kW: Capped at ₹78,000
        """
        if system_size_kw <= 0:
            return 0.0

        if system_size_kw <= 2.0:
            total = system_size_kw * self.rate_per_kw_upto_2
        elif system_size_kw <= 3.0:
            total = (2.0 * self.rate_per_kw_upto_2) + ((system_size_kw - 2.0) * self.rate_for_3rd_kw)
        else:
            total = (2.0 * self.rate_per_kw_upto_2) + (1.0 * self.rate_for_3rd_kw) + ((system_size_kw - 3.0) * self.rate_above_3rd_kw)

        return float(min(total, self.cap_amount))

    def __repr__(self):
        return f"<SubsidyRule {self.scheme_name} (Active: {self.effective_from} to {self.effective_to or 'Present'})>"


class PricingRule(Base):
    """
    Dated component and turnkey installation pricing for Vidarbha operations.
    Includes editable generation factor (1,400 kWh/kW/yr) and DISCOM unit tariffs.
    """

    __tablename__ = "pricing_rules"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(120), nullable=False)
    
    # Effective validity dates
    effective_from = Column(Date, nullable=False, default=date(2024, 1, 1))
    effective_to = Column(Date, nullable=True)

    # Base pricing per kW installed (turnkey: panels + tier-1 inverter + structure + net meter wiring)
    base_cost_per_kw = Column(Float, nullable=False, default=60000.0) # e.g. ₹60,000/kW

    # Generation factor from problem brief (1,400 units/kW/year, editable)
    generation_factor_kwh_per_kw = Column(Float, nullable=False, default=1400.0)

    # MSEDCL average retail residential tariff (₹ per kWh)
    tariff_per_kwh_rs = Column(Float, nullable=False, default=7.50)

    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<PricingRule {self.name} (₹{self.base_cost_per_kw:,.0f}/kW, {self.generation_factor_kwh_per_kw} kWh/yr)>"
