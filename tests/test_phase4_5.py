"""
SolarSync - Phase 4 & Phase 5 Automated Test Suite.
Tests dated subsidy rules, pricing engine, quote generation, PDF generation, and access control.
"""

import os
import pytest
import pytest_asyncio
from datetime import date
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.database import AsyncSessionLocal, engine
from app.models.lead import Lead, OwnershipType, RoofType, LeadStatus
from app.models.rules import SubsidyRule, PricingRule
from app.services.quote_service import (
    calculate_quote_financials,
    create_quote_for_lead,
    seed_default_rules,
)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_db_connections():
    """Ensure engine connections are disposed between test functions so event loops don't conflict."""
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_subsidy_rule_mathematics():
    """Verify exact PM Surya Ghar mathematical subsidy calculations across kW steps."""
    rule = SubsidyRule(
        scheme_name="PM Surya Ghar Muft Bijli Yojana",
        effective_from=date(2024, 2, 15),
        rate_per_kw_upto_2=30000.0,
        rate_for_3rd_kw=18000.0,
        rate_above_3rd_kw=0.0,
        cap_amount=78000.0,
        is_active=True,
    )

    # 0 kW
    assert rule.calculate_subsidy(0.0) == 0.0

    # 1 kW = 1 * 30,000 = 30,000
    assert rule.calculate_subsidy(1.0) == 30000.0

    # 2 kW = 2 * 30,000 = 60,000
    assert rule.calculate_subsidy(2.0) == 60000.0

    # 2.5 kW = 60,000 + (0.5 * 18,000) = 69,000
    assert rule.calculate_subsidy(2.5) == 69000.0

    # 3 kW = 60,000 + 18,000 = 78,000
    assert rule.calculate_subsidy(3.0) == 78000.0

    # 4 kW = Capped at 78,000
    assert rule.calculate_subsidy(4.0) == 78000.0

    # 5 kW = Capped at 78,000
    assert rule.calculate_subsidy(5.0) == 78000.0

    # 10 kW = Capped at 78,000
    assert rule.calculate_subsidy(10.0) == 78000.0


@pytest.mark.asyncio
async def test_financial_and_payback_formulas():
    """Verify formula calculations for gross cost, net cost, 1400 kWh generation, and payback."""
    subsidy_rule = SubsidyRule(
        scheme_name="Test Subsidy",
        effective_from=date(2024, 1, 1),
        rate_per_kw_upto_2=30000.0,
        rate_for_3rd_kw=18000.0,
        rate_above_3rd_kw=0.0,
        cap_amount=78000.0,
        is_active=True,
    )

    pricing_rule = PricingRule(
        name="Test Pricing",
        effective_from=date(2024, 1, 1),
        base_cost_per_kw=60000.0,
        generation_factor_kwh_per_kw=1400.0,
        tariff_per_kwh_rs=7.50,
        is_active=True,
    )

    # Test 3 kW system (Nagpur standard sweet spot)
    fin_3kw = calculate_quote_financials(3.0, subsidy_rule, pricing_rule)

    assert fin_3kw["system_size_kw"] == 3.0
    assert fin_3kw["gross_cost_rs"] == 180000.0      # 3 * 60,000
    assert fin_3kw["subsidy_amount_rs"] == 78000.0    # 60k + 18k
    assert fin_3kw["net_cost_rs"] == 102000.0         # 180k - 78k
    assert fin_3kw["yearly_generation_kwh"] == 4200   # 3 * 1,400
    assert fin_3kw["yearly_savings_rs"] == 31500.0    # 4,200 * 7.50
    assert fin_3kw["payback_years"] == 3.2            # 102,000 / 31,500 = 3.238 -> 3.2
    assert fin_3kw["lifetime_savings_25yr_rs"] == 685500.0 # (31,500 * 25) - 102,000 = 685,500


@pytest.mark.asyncio
async def test_quote_creation_and_pdf_generation():
    """Test generating a Quote record and compiling a real ReportLab PDF."""
    async with AsyncSessionLocal() as session:
        # Ensure rules exist
        await seed_default_rules(session)

        # Get or create a sample lead
        result = await session.execute(select(Lead).where(Lead.lead_id == "LD-30881"))
        lead = result.scalars().first()
        if not lead:
            lead = Lead(
                lead_id="LD-30881",
                name="Prashant Wankhede",
                phone="9823012345",
                pincode="440010",
                ownership=OwnershipType.OWNER,
                roof_type=RoofType.RCC_FLAT,
                roof_area_sqft=450.0,
                monthly_bill_rs=3200,
                status=LeadStatus.QUALIFIED,
            )
            session.add(lead)
            await session.commit()
            await session.refresh(lead)

        # Create quote
        quote = await create_quote_for_lead(
            db=session,
            lead=lead,
            system_size_kw=3.0,
            panel_specs="Mono PERC 550W Tier-1 (Half-Cut)",
            inverter_specs="High-Efficiency On-Grid String Inverter (IP65)",
            notes="Testing PDF generation",
        )

        assert quote.id is not None
        assert quote.quote_number.startswith("QT-30881-")
        assert quote.net_cost_rs == 102000.0
        assert quote.pdf_path is not None
        assert os.path.exists(quote.pdf_path)
        assert os.path.getsize(quote.pdf_path) > 1000  # Non-trivial PDF size

        # Verify PDF strictly fits on a single page
        import re
        with open(quote.pdf_path, "rb") as f:
            pdf_bytes = f.read()
        page_tags = re.findall(rb'/Type\s*/Page\b', pdf_bytes)
        assert len(page_tags) == 1, f"Expected single-page PDF, got {len(page_tags)} pages"


@pytest.mark.asyncio
async def test_admin_rules_endpoints_and_role_protection():
    """Verify only Admin can access /admin/rules; sales/homeowner are forbidden."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. Admin login
        admin_login = await ac.post(
            "/auth/login",
            data={"email": "admin@solarsync.in", "password": "admin123"},
            follow_redirects=False,
        )
        assert admin_login.status_code == 303
        admin_cookies = admin_login.cookies

        # 2. Sales login
        sales_login = await ac.post(
            "/auth/login",
            data={"email": "sales@solarsync.in", "password": "sales123"},
            follow_redirects=False,
        )
        assert sales_login.status_code == 303
        sales_cookies = sales_login.cookies

        # 3. Admin can access /admin/rules
        res = await ac.get("/admin/rules", cookies=admin_cookies)
        assert res.status_code == 200
        assert "Dated Rule Management" in res.text

        # 4. Sales is forbidden from /admin/rules (403)
        res_forbidden = await ac.get("/admin/rules", cookies=sales_cookies)
        assert res_forbidden.status_code == 403

        # 5. Sales can access Quotes list and Quote Builder
        res_quotes = await ac.get("/quotes", cookies=sales_cookies)
        assert res_quotes.status_code == 200
        assert "Solar Quotations" in res_quotes.text

        res_builder = await ac.get("/leads/LD-30881/quote/new", cookies=sales_cookies)
        assert res_builder.status_code == 200
        assert "Quote Builder" in res_builder.text
