"""SolarSync models package."""

from app.models.user import User, UserRole
from app.models.lead import (
    Lead,
    OwnershipType,
    RoofType,
    LeadStatus,
    LeadScore,
)
from app.models.bill_reading import BillReading
from app.models.rules import SubsidyRule, PricingRule
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

__all__ = [
    "User",
    "UserRole",
    "Lead",
    "OwnershipType",
    "RoofType",
    "LeadStatus",
    "LeadScore",
    "BillReading",
    "SubsidyRule",
    "PricingRule",
    "Quote",
    "Project",
    "ProjectStage",
    "ProjectDocument",
    "ProjectNotification",
    "ProjectStageHistory",
    "STAGE_METADATA",
    "STAGE_ORDER",
]
