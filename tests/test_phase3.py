"""
Tests for Phase 3: Lead Scoring & Pipeline Dashboard.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.scoring_service import evaluate_lead_score
from app.models.lead import OwnershipType, LeadScore


def test_scoring_rubric():
    # 1. High priority applicant: Prashant
    p = evaluate_lead_score(OwnershipType.OWNER, 450.0, 3200, 340, 4.0)
    assert p["tier"] == LeadScore.HIGH
    assert p["total_score"] >= 70
    assert p["saved_survey_cost_inr"] == 0

    # 2. Tenant applicant: Rohit
    r = evaluate_lead_score(OwnershipType.TENANT, 300.0, 1900, 210, 3.0)
    assert r["tier"] == LeadScore.LOW
    assert r["saved_survey_cost_inr"] == 2500
    assert "tenant" in r["action_note"].lower()

    # 3. Small bill applicant: Ayesha
    a = evaluate_lead_score(OwnershipType.OWNER, 200.0, 650, 85, 1.0)
    assert a["tier"] == LeadScore.LOW
    assert a["saved_survey_cost_inr"] == 2500
    assert "small" in a["action_note"].lower() or "payback" in a["action_note"].lower()


@pytest.mark.asyncio
async def test_leads_pipeline_and_actions():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Sales login
        login_res = await client.post(
            "/auth/login",
            data={"email": "sales@solarsync.in", "password": "sales123"},
            follow_redirects=False,
        )
        assert login_res.status_code == 303
        cookies = login_res.cookies

        # 1. Search by name
        res = await client.get("/leads?q=Prashant", cookies=cookies)
        assert res.status_code == 200
        assert "LD-30881" in res.text
        assert "LD-30902" not in res.text

        # 2. Search by PIN
        res = await client.get("/leads?q=440010", cookies=cookies)
        assert res.status_code == 200
        assert "440010" in res.text

        # 3. Filter by score
        res = await client.get("/leads?score=low", cookies=cookies)
        assert res.status_code == 200
        assert "LD-30902" in res.text
        assert "LD-30915" in res.text

        # 4. Action: Schedule survey
        act = await client.post(
            "/leads/LD-30881/action",
            data={"action": "schedule_survey"},
            cookies=cookies,
            follow_redirects=False,
        )
        assert act.status_code == 303

        # 5. Detail page shows transparent scorecard
        det = await client.get("/leads/LD-30881", cookies=cookies)
        assert det.status_code == 200
        assert "Survey Visit Booked" in det.text
        assert "Qualification Score" in det.text
        assert "Property Ownership" in det.text

        # 6. Dashboard metrics
        dash = await client.get("/dashboard", cookies=cookies)
        assert dash.status_code == 200
        assert "Financial Impact" in dash.text or "Survey Costs Eliminated" in dash.text
        assert "Booked Surveys" in dash.text
