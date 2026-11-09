"""
SolarSync - Project tracker service layer.
Handles milestone progression, stuck project detection, document attachments, and mock WhatsApp/SMS alerts.
"""

import os
import shutil
import secrets
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from fastapi import UploadFile
from sqlalchemy import select, desc, or_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.lead import Lead, LeadStatus
from app.models.quote import Quote
from app.models.project import (
    Project,
    ProjectStage,
    ProjectDocument,
    ProjectNotification,
    ProjectStageHistory,
    STAGE_METADATA,
    STAGE_ORDER,
)

settings = get_settings()


async def get_project_by_token(db: AsyncSession, public_token: str) -> Optional[Project]:
    """Fetch project by secure customer tracking token without requiring authentication."""
    query = (
        select(Project)
        .options(
            selectinload(Project.lead),
            selectinload(Project.quote),
            selectinload(Project.documents),
            selectinload(Project.notifications),
            selectinload(Project.stage_history),
        )
        .where(Project.public_token == public_token)
    )
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def get_project_by_identifier(db: AsyncSession, identifier: str) -> Optional[Project]:
    """Fetch project by formatted project_id (e.g. PRJ-30881) or integer primary key."""
    query = select(Project).options(
        selectinload(Project.lead),
        selectinload(Project.quote),
        selectinload(Project.documents),
        selectinload(Project.notifications),
        selectinload(Project.stage_history),
    )
    if identifier.isdigit():
        query = query.where(Project.id == int(identifier))
    else:
        query = query.where(Project.project_id == identifier)
    result = await db.execute(query)
    return result.scalar_one_or_none()


async def list_projects(
    db: AsyncSession,
    stage: Optional[ProjectStage] = None,
    only_stuck: bool = False,
    search_query: Optional[str] = None,
    skip: int = 0,
    limit: int = 50,
) -> List[Project]:
    """List projects with flexible stage filtering, search, and stuck project detection."""
    query = (
        select(Project)
        .options(
            selectinload(Project.lead),
            selectinload(Project.quote),
            selectinload(Project.documents),
        )
        .order_by(desc(Project.created_at))
    )

    if stage:
        query = query.where(Project.stage == stage)

    result = await db.execute(query)
    projects = list(result.scalars().all())

    # Filter stuck if requested
    if only_stuck:
        projects = [p for p in projects if p.is_stuck]

    # Search filter by customer name, phone, or project ID
    if search_query and search_query.strip():
        q = search_query.strip().lower()
        projects = [
            p for p in projects
            if (p.lead and (q in p.lead.name.lower() or q in p.lead.phone or q in p.lead.pincode))
            or q in p.project_id.lower()
        ]

    return projects[skip : skip + limit]


async def send_mock_stage_notification(
    db: AsyncSession,
    project: Project,
    stage: ProjectStage,
    channel: str = "whatsapp",
) -> ProjectNotification:
    """Simulate sending a WhatsApp or SMS alert to the customer when a project milestone shifts."""
    meta = STAGE_METADATA[stage]
    lead = await db.get(Lead, project.lead_id)
    lead_name = lead.name if lead else "Customer"
    customer_phone = lead.phone if lead else "9800000000"

    message = (
        f"☀️ *SolarSync · Company A Nagpur*\n"
        f"Namaskar {lead_name},\n\n"
        f"Great news! Your rooftop solar project *{project.project_id}* has progressed to a new milestone:\n"
        f"📍 *{meta['title_en']}*\n"
        f"🚩 *{meta['title_mr']}*\n\n"
        f"📝 *Update:* {meta['description']}\n"
        f"⚡ *Next Step:* {meta['action_required']}\n\n"
        f"🔗 *Track live status (no login required):*\n"
        f"{settings.BASE_URL}/track/{project.public_token}\n\n"
        f"Need assistance? Contact project coordinator: +91 {project.installer_phone}"
    )

    notification = ProjectNotification(
        project_id=project.id,
        stage=stage,
        channel=channel,
        recipient_phone=customer_phone,
        message_text=message,
        status="delivered",
    )
    db.add(notification)
    return notification


async def create_project_for_lead(
    db: AsyncSession,
    lead: Lead,
    quote: Optional[Quote] = None,
    advance_amount_rs: float = 25000.0,
    assigned_engineer: Optional[str] = None,
    notes: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Project:
    """Convert an accepted quotation or qualified lead into an active tracked installation project."""
    # Check if a project already exists for this lead
    res = await db.execute(select(Project).where(Project.lead_id == lead.id))
    existing = res.scalars().first()
    if existing:
        return existing

    # Generate project ID
    lead_code = lead.lead_id.replace("LD-", "")
    project_id = f"PRJ-{lead_code}"

    project = Project(
        project_id=project_id,
        lead_id=lead.id,
        quote_id=quote.id if quote else None,
        public_token=secrets.token_urlsafe(24),
        stage=ProjectStage.ADVANCE_PAID,
        stage_updated_at=datetime.utcnow(),
        advance_amount_rs=advance_amount_rs,
        assigned_engineer=assigned_engineer or "Suresh Patil (Senior Technical Engineer, Vidarbha)",
        installer_phone="9823055667",
        stuck_days_threshold=5,
        notes=notes or f"Rooftop solar project initiated for {lead.name}.",
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    # Initial history log
    history = ProjectStageHistory(
        project_id=project.id,
        from_stage=None,
        to_stage=ProjectStage.ADVANCE_PAID,
        notes=f"Project initiated upon advance receipt of Rs. {advance_amount_rs:,.0f}.",
        changed_by_id=user_id,
    )
    db.add(history)

    # Send initial mock WhatsApp notification
    await send_mock_stage_notification(db, project, ProjectStage.ADVANCE_PAID, channel="whatsapp")

    # Update lead status
    lead.status = LeadStatus.SURVEY_SCHEDULED
    await db.commit()
    await db.refresh(project)
    return project


async def advance_project_stage(
    db: AsyncSession,
    project: Project,
    new_stage: ProjectStage,
    user_id: Optional[int] = None,
    notes: Optional[str] = None,
) -> Project:
    """Transition project to the next operational milestone and trigger customer notifications."""
    old_stage = project.stage
    project.stage = new_stage
    project.stage_updated_at = datetime.utcnow()

    # Log history
    history = ProjectStageHistory(
        project_id=project.id,
        from_stage=old_stage,
        to_stage=new_stage,
        notes=notes or f"Milestone transitioned from {old_stage.value} to {new_stage.value}.",
        changed_by_id=user_id,
    )
    db.add(history)

    # Send mock WhatsApp & SMS alerts to customer
    await send_mock_stage_notification(db, project, new_stage, channel="whatsapp")
    await send_mock_stage_notification(db, project, new_stage, channel="sms")

    await db.commit()
    await db.refresh(project)
    return project


async def upload_project_document(
    db: AsyncSession,
    project: Project,
    file: UploadFile,
    document_type: str,
    title: str,
    user_id: Optional[int] = None,
) -> ProjectDocument:
    """Save an engineering or DISCOM document attached to the project."""
    target_dir = os.path.join(settings.UPLOAD_DIR, "projects", project.project_id)
    os.makedirs(target_dir, exist_ok=True)

    filename = f"{document_type}_{int(datetime.utcnow().timestamp())}_{file.filename}"
    file_disk_path = os.path.join(target_dir, filename)
    rel_path = f"uploads/projects/{project.project_id}/{filename}"

    with open(file_disk_path, "wb") as f_out:
        shutil.copyfileobj(file.file, f_out)

    size_kb = int(os.path.getsize(file_disk_path) / 1024)

    doc = ProjectDocument(
        project_id=project.id,
        document_type=document_type,
        title=title,
        file_path=rel_path,
        file_size_kb=size_kb,
        uploaded_by_id=user_id,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def seed_sample_projects(db: AsyncSession):
    """Seed realistic operational projects showcasing normal progress, stuck state, and completion."""
    # 1. Project 1 (Prashant Wankhede): PRJ-30881
    res_prashant = await db.execute(select(Lead).where(Lead.lead_id == "LD-30881"))
    prashant_lead = res_prashant.scalars().first()
    if prashant_lead:
        res_p1 = await db.execute(select(Project).where(Project.project_id == "PRJ-30881"))
        proj1 = res_p1.scalars().first()
        if not proj1:
            res_quote = await db.execute(select(Quote).where(Quote.lead_id == prashant_lead.id))
            prashant_quote = res_quote.scalars().first()
            proj1 = Project(
                project_id="PRJ-30881",
                lead_id=prashant_lead.id,
                quote_id=prashant_quote.id if prashant_quote else None,
                public_token="demo-prashant-token-30881",
                stage=ProjectStage.INSTALLATION,
                stage_updated_at=datetime.utcnow() - timedelta(days=2),
                advance_amount_rs=25000.0,
                assigned_engineer="Suresh Patil (Lead Engineer)",
                installer_phone="9823055667",
                notes="Structure elevated 6ft on terrace. Panels mounted, string inverter wiring in progress.",
            )
            db.add(proj1)
            await db.flush()

            doc1 = ProjectDocument(
                project_id=proj1.id,
                document_type="survey_report",
                title="Technical Feasibility & Shadow Analysis Report (Nagpur)",
                file_path="uploads/projects/sample_survey_report.pdf",
                file_size_kb=420,
            )
            doc2 = ProjectDocument(
                project_id=proj1.id,
                document_type="advance_receipt",
                title="Advance Token Payment Receipt (₹25,000)",
                file_path="uploads/projects/sample_advance_receipt.pdf",
                file_size_kb=150,
            )
            db.add_all([doc1, doc2])
            await send_mock_stage_notification(db, proj1, ProjectStage.INSTALLATION, channel="whatsapp")

    # 2. Project 2 (Demonstration of STUCK PROJECT): Idle at NET_METERING for 8 days!
    res_p2 = await db.execute(select(Project).where(Project.project_id == "PRJ-30940"))
    if not res_p2.scalars().first():
        res_stuck_lead = await db.execute(select(Lead).where(Lead.lead_id == "LD-30940"))
        stuck_lead = res_stuck_lead.scalars().first()
        if not stuck_lead:
            stuck_lead = Lead(
                lead_id="LD-30940",
                name="Vinod Deshmukh",
                phone="9822188990",
                pincode="440015",
                monthly_bill_rs=4500,
                roof_area_sqft=600.0,
                bill_photo_path="uploads/bills/sample_bill_30881.jpg",
                status=LeadStatus.SURVEY_SCHEDULED,
            )
            db.add(stuck_lead)
            await db.flush()

        proj_stuck = Project(
            project_id="PRJ-30940",
            lead_id=stuck_lead.id,
            public_token="demo-stuck-token-30940",
            stage=ProjectStage.NET_METERING,
            stage_updated_at=datetime.utcnow() - timedelta(days=8), # 8 days idle (> 5 days threshold = STUCK!)
            advance_amount_rs=35000.0,
            assigned_engineer="Prakash Joshi (Field Liaison)",
            installer_phone="9823099112",
            notes="Awaiting MSEDCL sub-divisional inspector for bi-directional meter release. Inspector follow-up pending.",
        )
        db.add(proj_stuck)
        await db.flush()

        doc_stuck = ProjectDocument(
            project_id=proj_stuck.id,
            document_type="installation_photo",
            title="Physical Rooftop Installation Handover Photo",
            file_path="uploads/projects/sample_installation.jpg",
            file_size_kb=850,
        )
        db.add(doc_stuck)
        await send_mock_stage_notification(db, proj_stuck, ProjectStage.NET_METERING, channel="whatsapp")

    # 3. Project 3: Fully completed (SUBSIDY_CREDITED)
    res_p3 = await db.execute(select(Project).where(Project.project_id == "PRJ-30799"))
    if not res_p3.scalars().first():
        res_comp_lead = await db.execute(select(Lead).where(Lead.lead_id == "LD-30799"))
        comp_lead = res_comp_lead.scalars().first()
        if not comp_lead:
            comp_lead = Lead(
                lead_id="LD-30799",
                name="Sneha Kulkarni",
                phone="9823077881",
                pincode="440022",
                monthly_bill_rs=3800,
                roof_area_sqft=500.0,
                bill_photo_path="uploads/bills/sample_bill_30881.jpg",
                status=LeadStatus.SURVEY_SCHEDULED,
            )
            db.add(comp_lead)
            await db.flush()

        proj_comp = Project(
            project_id="PRJ-30799",
            lead_id=comp_lead.id,
            public_token="demo-complete-token-30799",
            stage=ProjectStage.SUBSIDY_CREDITED,
            stage_updated_at=datetime.utcnow() - timedelta(days=1),
            advance_amount_rs=30000.0,
            assigned_engineer="Suresh Patil",
            installer_phone="9823055667",
            notes="Central DBT subsidy of ₹78,000 credited to customer bank account. 25-year warranty package delivered.",
        )
        db.add(proj_comp)
        await db.flush()

        doc_comp = ProjectDocument(
            project_id=proj_comp.id,
            document_type="subsidy_credit_proof",
            title="PM Surya Ghar Central DBT Bank Credit Acknowledgement",
            file_path="uploads/projects/sample_subsidy_credit.pdf",
            file_size_kb=280,
        )
        db.add(doc_comp)
        await send_mock_stage_notification(db, proj_comp, ProjectStage.SUBSIDY_CREDITED, channel="whatsapp")

    await db.commit()
