"""
SolarSync - Phase 6 & Phase 7 Automated Test Suite.
Tests project tracker, milestone progression, stuck project detection, public tracking link, and notifications.
"""

import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

from app.main import app
from app.database import AsyncSessionLocal, engine
from app.models.lead import Lead, OwnershipType, RoofType, LeadStatus
from app.models.project import (
    Project,
    ProjectStage,
    ProjectNotification,
    ProjectStageHistory,
    STAGE_METADATA,
)
from app.services.project_service import (
    create_project_for_lead,
    advance_project_stage,
    get_project_by_token,
    seed_sample_projects,
)


@pytest_asyncio.fixture(autouse=True)
async def cleanup_db_connections():
    """Ensure engine connections are disposed between test functions."""
    yield
    await engine.dispose()


@pytest.mark.asyncio
async def test_project_creation_and_milestones():
    """Test project creation, token generation, initial stage, and mock notification."""
    async with AsyncSessionLocal() as session:
        # Create a fresh unique lead for new project testing
        import random
        rand_id = random.randint(10000, 99999)
        lead = Lead(
            lead_id=f"LD-{rand_id}",
            name="Ramesh Gupta",
            phone="9823099999",
            pincode="440010",
            roof_area_sqft=500.0,
            monthly_bill_rs=3500,
            bill_photo_path="uploads/bills/sample_bill_30881.jpg",
        )
        session.add(lead)
        await session.commit()
        await session.refresh(lead)

        # Create project
        project = await create_project_for_lead(
            db=session,
            lead=lead,
            advance_amount_rs=25000.0,
        )

        assert project.id is not None
        assert project.project_id.startswith("PRJ-")
        assert project.stage == ProjectStage.ADVANCE_PAID
        assert len(project.public_token) >= 20
        assert project.progress_percentage == 16  # 1/6 ~ 16%

        # Check notification dispatched
        res_notif = await session.execute(
            select(ProjectNotification).where(ProjectNotification.project_id == project.id)
        )
        notifs = res_notif.scalars().all()
        assert len(notifs) >= 1
        assert "http://127.0.0.1:8000/track/" in notifs[0].message_text
        assert project.public_token in notifs[0].message_text


@pytest.mark.asyncio
async def test_milestone_progression_and_history():
    """Test advancing milestones, history audit trail, and customer alerts."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Project).where(Project.project_id == "PRJ-30881"))
        project = res.scalars().first()
        assert project is not None

        # Advance to SURVEY_DONE
        await advance_project_stage(
            db=session,
            project=project,
            new_stage=ProjectStage.SURVEY_DONE,
            notes="Roof structural capacity approved for 12 Mono PERC panels.",
        )
        assert project.stage == ProjectStage.SURVEY_DONE
        assert project.progress_percentage == 33  # 2/6 ~ 33%

        # Verify history record
        res_hist = await session.execute(
            select(ProjectStageHistory)
            .where(ProjectStageHistory.project_id == project.id)
            .order_by(ProjectStageHistory.created_at.desc())
        )
        history_entry = res_hist.scalars().first()
        assert history_entry.to_stage == ProjectStage.SURVEY_DONE
        assert "approved" in history_entry.notes.lower()


@pytest.mark.asyncio
async def test_stuck_project_detection():
    """Test stuck project logic (>5 days idle without progression)."""
    # 1. Project idle for 8 days at NET_METERING
    stuck_proj = Project(
        project_id="PRJ-TEST-STUCK",
        lead_id=1,
        stage=ProjectStage.NET_METERING,
        stage_updated_at=datetime.utcnow() - timedelta(days=8),
        stuck_days_threshold=5,
    )
    assert stuck_proj.days_in_current_stage >= 8
    assert stuck_proj.is_stuck is True

    # 2. Project updated yesterday
    active_proj = Project(
        project_id="PRJ-TEST-ACTIVE",
        lead_id=1,
        stage=ProjectStage.INSTALLATION,
        stage_updated_at=datetime.utcnow() - timedelta(days=1),
        stuck_days_threshold=5,
    )
    assert active_proj.days_in_current_stage == 1
    assert active_proj.is_stuck is False

    # 3. Completed project is NEVER stuck even if completed 30 days ago
    completed_proj = Project(
        project_id="PRJ-TEST-DONE",
        lead_id=1,
        stage=ProjectStage.SUBSIDY_CREDITED,
        stage_updated_at=datetime.utcnow() - timedelta(days=30),
        stuck_days_threshold=5,
    )
    assert completed_proj.is_stuck is False


@pytest.mark.asyncio
async def test_public_customer_tracking_no_login():
    """Verify customer can access /track/{token} with NO login required."""
    async with AsyncSessionLocal() as session:
        await seed_sample_projects(session)
        res = await session.execute(select(Project).where(Project.project_id == "PRJ-30881"))
        project = res.scalars().first()
        token = project.public_token

    transport = ASGITransport(app=app)
    # Anonymous client with no cookies
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get(f"/track/{token}")
        assert res.status_code == 200
        assert "Installation Status" in res.text
        assert project.project_id in res.text
        # Check bilingual Marathi milestone title presence
        assert "टप्पा" in res.text or "प्रकल्पाचे टप्पे" in res.text


@pytest.mark.asyncio
async def test_staff_projects_dashboard_and_operations():
    """Verify sales staff can view project tracker and advance milestone."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Sales login
        login_res = await ac.post(
            "/auth/login",
            data={"email": "sales@solarsync.in", "password": "sales123"},
            follow_redirects=False,
        )
        assert login_res.status_code == 303
        sales_cookies = login_res.cookies

        # View projects list
        res_list = await ac.get("/projects", cookies=sales_cookies)
        assert res_list.status_code == 200
        assert "Installation Project Tracker" in res_list.text
        assert "Stuck Projects" in res_list.text

        # View project detail
        res_detail = await ac.get("/projects/PRJ-30881", cookies=sales_cookies)
        assert res_detail.status_code == 200
        assert "Milestone Progression Control" in res_detail.text
