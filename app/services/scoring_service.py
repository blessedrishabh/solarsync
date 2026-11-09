"""
SolarSync - Lead Scoring Engine and Business ROI Analytics.
Computes multi-factor qualification scores (0-100) and tracks survey cost savings for Company A, Nagpur.
"""

from typing import Dict, Any, Optional
from app.models.lead import Lead, LeadScore, OwnershipType


SURVEY_COST_INR = 2500  # Cost per surveyor site visit in Vidarbha
ANNUAL_GEN_PER_KW = 1400  # kWh per kW per year (Central Residential formula)


def evaluate_lead_score(
    ownership: OwnershipType,
    roof_area_sqft: float,
    monthly_bill_rs: int,
    monthly_units: Optional[int] = None,
    sanctioned_load_kw: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Multi-factor scoring rubric matching Company A's business realities:
    1. Ownership (35 pts): Owner (35), Tenant (0) - Tenants cannot install solar.
    2. Consumption & Bill (35 pts):
       - Units >= 300 or Bill >= 3,000 -> 35 pts
       - Units 200-299 or Bill 2,000-2,999 -> 25 pts
       - Units 100-199 or Bill 1,000-1,999 -> 15 pts
       - Units < 100 or Bill < 1,000 -> 0 pts
    3. Roof Adequacy (20 pts):
       - Roof >= 400 sq ft -> 20 pts
       - Roof 250-399 sq ft -> 15 pts
       - Roof 100-249 sq ft -> 10 pts
       - Roof < 100 sq ft -> 0 pts
    4. Sanctioned Load Match (10 pts):
       - Load >= 3 kW -> 10 pts
       - Load 1-2 kW -> 5 pts
       - Unknown -> 5 pts

    Total Score: 0 - 100
    - Score >= 70: HIGH (Schedule on-site survey visit immediately)
    - Score 40 - 69: MEDIUM (Conduct short phone call first to verify interest)
    - Score < 40: LOW (Replaced survey with short call, saving ₹2,500)
    """
    factors = []
    total_points = 0

    # 1. Ownership Factor
    if ownership == OwnershipType.OWNER or str(ownership).lower() == "owner":
        ownership_pts = 35
        factors.append({
            "name": "Property Ownership",
            "points": 35,
            "max_points": 35,
            "status": "pass",
            "detail": "Verified property owner with roof rights",
        })
    else:
        ownership_pts = 0
        factors.append({
            "name": "Property Ownership",
            "points": 0,
            "max_points": 35,
            "status": "fail",
            "detail": "Tenant / Rented property (requires landlord NOC)",
        })
    total_points += ownership_pts

    # 2. Consumption / Bill Factor
    effective_units = monthly_units if monthly_units else int(monthly_bill_rs / 8.0)
    if effective_units >= 300 or monthly_bill_rs >= 3000:
        bill_pts = 35
        bill_status = "pass"
        bill_detail = f"High consumption ({effective_units} units/mo, ₹{monthly_bill_rs:,}) - excellent solar ROI"
    elif effective_units >= 200 or monthly_bill_rs >= 2000:
        bill_pts = 25
        bill_status = "pass"
        bill_detail = f"Moderate-high consumption ({effective_units} units/mo, ₹{monthly_bill_rs:,})"
    elif effective_units >= 100 or monthly_bill_rs >= 1000:
        bill_pts = 15
        bill_status = "warn"
        bill_detail = f"Moderate consumption ({effective_units} units/mo, ₹{monthly_bill_rs:,}) - payback ~5-6 yrs"
    else:
        bill_pts = 0
        bill_status = "fail"
        bill_detail = f"Small bill ({effective_units} units/mo, ₹{monthly_bill_rs:,}) - payback exceeds 8-10 yrs"
    
    factors.append({
        "name": "Electricity Bill & Units",
        "points": bill_pts,
        "max_points": 35,
        "status": bill_status,
        "detail": bill_detail,
    })
    total_points += bill_pts

    # 3. Roof Adequacy Factor
    if roof_area_sqft >= 400:
        roof_pts = 20
        roof_status = "pass"
        roof_detail = f"{roof_area_sqft:,.0f} sq ft roof accommodates 3-5 kW system easily"
    elif roof_area_sqft >= 250:
        roof_pts = 15
        roof_status = "pass"
        roof_detail = f"{roof_area_sqft:,.0f} sq ft roof accommodates 2-3 kW system"
    elif roof_area_sqft >= 100:
        roof_pts = 10
        roof_status = "warn"
        roof_detail = f"{roof_area_sqft:,.0f} sq ft roof accommodates 1 kW system"
    else:
        roof_pts = 0
        roof_status = "fail"
        roof_detail = f"{roof_area_sqft:,.0f} sq ft roof is too small for standard solar panels"

    factors.append({
        "name": "Usable Roof Area",
        "points": roof_pts,
        "max_points": 20,
        "status": roof_status,
        "detail": roof_detail,
    })
    total_points += roof_pts

    # 4. Sanctioned Load Factor
    if sanctioned_load_kw and sanctioned_load_kw >= 3.0:
        load_pts = 10
        load_detail = f"{sanctioned_load_kw} kW sanctioned load supports desired generation"
    elif sanctioned_load_kw:
        load_pts = 5
        load_detail = f"{sanctioned_load_kw} kW load (may need DISCOM load enhancement)"
    else:
        load_pts = 5
        load_detail = "Sanctioned load not yet verified from bill"

    factors.append({
        "name": "Sanctioned Grid Load",
        "points": load_pts,
        "max_points": 10,
        "status": "pass" if load_pts >= 10 else "info",
        "detail": load_detail,
    })
    total_points += load_pts

    # Determine Tier & Strategic Action per Company A Vidarbha Rules
    if ownership_pts == 0:
        # 1. Tenant: automatic Low score regardless of bill
        tier = LeadScore.LOW
        action = "Short Qualification Call"
        action_note = "Do NOT send surveyor. Call tenant to check if landlord will co-sign agreement. Saves ₹2,500 site visit."
        rec_kw = None
        saved_survey_cost = SURVEY_COST_INR
    elif monthly_bill_rs < 800 or effective_units < 100:
        # 2. Small bill (< ₹800): automatic Low score per brief
        tier = LeadScore.LOW
        action = "Short Advisory Call"
        action_note = f"Small monthly bill ({effective_units} units/mo, ₹{monthly_bill_rs:,}). Solar payback period is too long to justify a ₹2,500 site visit. Replaced survey with a short phone call."
        rec_kw = 1.0
        saved_survey_cost = SURVEY_COST_INR
    elif total_points >= 70:
        # 3. High score: genuine owner with substantial consumption
        tier = LeadScore.HIGH
        action = "Schedule Site Survey Visit"
        action_note = "High conversion probability. Book surveyor visit for rooftop shadow analysis and quote finalization."
        rec_kw = float(max(1, min(round(effective_units / 116.0), int(roof_area_sqft / 100.0))))
        saved_survey_cost = 0
    elif total_points >= 40:
        # 4. Medium score: moderate consumption, phone screening first
        tier = LeadScore.MEDIUM
        action = "Phone Screening First"
        action_note = "Moderate bill/roof. Conduct a 5-minute phone screening to verify budget before booking ₹2,500 surveyor visit."
        rec_kw = float(max(1, min(round(effective_units / 116.0), int(roof_area_sqft / 100.0))))
        saved_survey_cost = 0
    else:
        tier = LeadScore.LOW
        action = "Short Advisory Call"
        action_note = "Low feasibility score. Explain solar options over phone rather than wasting ₹2,500 on an on-site survey."
        rec_kw = 1.0
        saved_survey_cost = SURVEY_COST_INR

    return {
        "total_score": total_points,
        "tier": tier,
        "action": action,
        "action_note": action_note,
        "factors": factors,
        "recommended_kw": rec_kw,
        "saved_survey_cost_inr": saved_survey_cost,
    }
