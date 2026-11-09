"""
SolarSync - Project tracker models for rooftop solar installations.
Tracks execution stages: Advance Paid -> Survey Done -> Installation -> Net Metering -> Subsidy Applied -> Subsidy Credited.
Includes stuck project detection, document attachments, mock SMS/WhatsApp notifications, and public status tokens.
"""

import enum
import secrets
from datetime import datetime
from typing import Dict, Any, List
from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, ForeignKey, Text, Enum as SQLEnum
)
from sqlalchemy.orm import relationship
from app.database import Base


class ProjectStage(str, enum.Enum):
    """The 6 sequential rooftop solar stages specified in Company A brief."""
    ADVANCE_PAID = "advance_paid"          # Stage 1: Token/Advance Payment Received
    SURVEY_DONE = "survey_done"            # Stage 2: Technical Site Feasibility & Shadow Survey
    INSTALLATION = "installation"          # Stage 3: Panels, Inverter & Structure Installed
    NET_METERING = "net_metering"          # Stage 4: MSEDCL Net-Meter Installed & Synchronized
    SUBSIDY_APPLIED = "subsidy_applied"    # Stage 5: PM Surya Ghar National Portal DBT Filed
    SUBSIDY_CREDITED = "subsidy_credited"  # Stage 6: Central Residential Subsidy Credited to Bank


STAGE_ORDER: List[ProjectStage] = [
    ProjectStage.ADVANCE_PAID,
    ProjectStage.SURVEY_DONE,
    ProjectStage.INSTALLATION,
    ProjectStage.NET_METERING,
    ProjectStage.SUBSIDY_APPLIED,
    ProjectStage.SUBSIDY_CREDITED,
]

STAGE_METADATA: Dict[ProjectStage, Dict[str, Any]] = {
    ProjectStage.ADVANCE_PAID: {
        "index": 1,
        "title_en": "Advance Payment Received",
        "title_mr": "अ‍ॅडव्हान्स जमा (Advance Received)",
        "sla_days": 2,
        "description": "Initial advance received. Technical survey team dispatched for structural & shadow analysis.",
        "icon": "receipt",
        "action_required": "Schedule site survey engineer",
        "doc_type": "advance_receipt",
    },
    ProjectStage.SURVEY_DONE: {
        "index": 2,
        "title_en": "Technical Site Survey Done",
        "title_mr": "तांत्रिक पाहणी पूर्ण (Survey Complete)",
        "sla_days": 3,
        "description": "Roof structural stability, South tilt angle, and shadow mapping verified. Bill of Materials finalized.",
        "icon": "clipboard-check",
        "action_required": "Procure and dispatch solar equipment to site",
        "doc_type": "survey_report",
    },
    ProjectStage.INSTALLATION: {
        "index": 3,
        "title_en": "Rooftop Installation Completed",
        "title_mr": "सोलर इन्स्टॉलेशन पूर्ण (Installation Complete)",
        "sla_days": 5,
        "description": "Mono PERC panels, on-grid inverter, GI elevated structure, and AC/DC safety boxes installed and tested.",
        "icon": "solar-panel",
        "action_required": "Submit Work Completion Report & test documentation to MSEDCL",
        "doc_type": "installation_photo",
    },
    ProjectStage.NET_METERING: {
        "index": 4,
        "title_en": "Net Metering Synchronized",
        "title_mr": "नेट मीटरिंग सुरू (Net Metering Live)",
        "sla_days": 7,
        "description": "MSEDCL bi-directional net meter installed and grid synchronized. Clean solar power generation is officially live!",
        "icon": "bolt",
        "action_required": "Upload signed Net Meter Commissioning Certificate & file central DBT subsidy",
        "doc_type": "net_meter_report",
    },
    ProjectStage.SUBSIDY_APPLIED: {
        "index": 5,
        "title_en": "PM Surya Ghar Subsidy Filed",
        "title_mr": "अनुदान अर्ज सादर (Subsidy Claimed)",
        "sla_days": 3,
        "description": "Direct Benefit Transfer (DBT) claim successfully filed on National Rooftop Solar Portal with Discom joint inspection report.",
        "icon": "file-text",
        "action_required": "Monitor national DBT portal verification status",
        "doc_type": "subsidy_acknowledgement",
    },
    ProjectStage.SUBSIDY_CREDITED: {
        "index": 6,
        "title_en": "Subsidy Credited to Bank Account",
        "title_mr": "अनुदान खात्यात जमा (Subsidy Credited)",
        "sla_days": 15,
        "description": "Central residential subsidy (up to ₹78,000) credited directly to customer's Aadhaar-linked bank account via DBT.",
        "icon": "check-circle",
        "action_required": "Project fully executed · Hand over 25-year warranty cards",
        "doc_type": "subsidy_credit_proof",
    },
}


class Project(Base):
    """Execution tracker for rooftop solar installation projects."""

    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(String(30), unique=True, index=True, nullable=False) # e.g. PRJ-30881

    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=False)
    lead = relationship("Lead", backref="projects", lazy="selectin")

    quote_id = Column(Integer, ForeignKey("quotes.id"), nullable=True)
    quote = relationship("Quote", backref="projects", lazy="selectin")

    # Secure unguessable token for homeowner public tracking (NO LOGIN NEEDED)
    public_token = Column(String(64), unique=True, index=True, nullable=False, default=lambda: secrets.token_urlsafe(24))

    stage = Column(SQLEnum(ProjectStage), default=ProjectStage.ADVANCE_PAID, nullable=False)
    stage_updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    advance_amount_rs = Column(Float, default=25000.0, nullable=False)
    assigned_engineer = Column(String(120), default="Suresh Patil (Technical Lead, Nagpur)")
    installer_phone = Column(String(20), default="9823055667")

    # Stuck threshold in days (e.g. idle > 5 days at a stage flags project as stuck)
    stuck_days_threshold = Column(Integer, default=5, nullable=False)

    notes = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    documents = relationship("ProjectDocument", back_populates="project", cascade="all, delete-orphan", order_by="desc(ProjectDocument.uploaded_at)")
    notifications = relationship("ProjectNotification", back_populates="project", cascade="all, delete-orphan", order_by="desc(ProjectNotification.sent_at)")
    stage_history = relationship("ProjectStageHistory", back_populates="project", cascade="all, delete-orphan", order_by="desc(ProjectStageHistory.created_at)")

    @property
    def days_in_current_stage(self) -> int:
        """Number of full days spent at current stage without progression."""
        delta = datetime.utcnow() - self.stage_updated_at
        return max(0, delta.days)

    @property
    def is_stuck(self) -> bool:
        """Flags project as STUCK if idle beyond threshold and not completed."""
        if self.stage == ProjectStage.SUBSIDY_CREDITED:
            return False
        return self.days_in_current_stage >= self.stuck_days_threshold

    @property
    def progress_percentage(self) -> int:
        """Return percentage of milestone completion (16% to 100%)."""
        idx = STAGE_METADATA[self.stage]["index"]
        return int((idx / len(STAGE_ORDER)) * 100)

    @property
    def current_meta(self) -> Dict[str, Any]:
        """Convenience property for current stage display metadata."""
        return STAGE_METADATA[self.stage]

    def __repr__(self):
        return f"<Project {self.project_id}: Stage={self.stage.value} (Stuck={self.is_stuck})>"


class ProjectDocument(Base):
    """Uploaded engineering, DISCOM, and subsidy documents attached to project."""

    __tablename__ = "project_documents"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    project = relationship("Project", back_populates="documents")

    document_type = Column(String(50), nullable=False) # e.g. "net_meter_report", "survey_report"
    title = Column(String(150), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_size_kb = Column(Integer, default=0)

    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_by = relationship("User")
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ProjectNotification(Base):
    """Log of mock WhatsApp & SMS alerts sent to customer on stage milestones."""

    __tablename__ = "project_notifications"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    project = relationship("Project", back_populates="notifications")

    stage = Column(SQLEnum(ProjectStage), nullable=False)
    channel = Column(String(20), default="whatsapp", nullable=False) # "whatsapp", "sms"
    recipient_phone = Column(String(20), nullable=False)
    message_text = Column(Text, nullable=False)

    status = Column(String(30), default="delivered", nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ProjectStageHistory(Base):
    """Audit log of milestone progression."""

    __tablename__ = "project_stage_history"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    project = relationship("Project", back_populates="stage_history")

    from_stage = Column(SQLEnum(ProjectStage), nullable=True)
    to_stage = Column(SQLEnum(ProjectStage), nullable=False)
    notes = Column(Text, nullable=True)

    changed_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    changed_by = relationship("User")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
