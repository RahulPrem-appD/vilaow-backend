"""The domain tables, one module per aggregate.

This was a single 496-line module. Split, a change to introductions cannot
touch the agreement table, and each file says what it is for.

Everything is re-exported here, which is also what makes the split safe:
SQLAlchemy resolves `relationship("ProfessionField")` by name at mapper
configuration time, so every model simply has to have been imported by then —
and importing this package does exactly that.
"""
from __future__ import annotations

from app.models.base import Base, utcnow
from app.models.staff import Role, Staff
from app.models.profession import FieldType, Profession, ProfessionField
from app.models.professional import ImportBatch, Professional, Stage
from app.models.agreement import Agreement
from app.models.lead import Lead, LeadStatus
from app.models.event import Event
from app.models.review import Review, ReviewKind
from app.models.introduction import Introduction, IntroOutcome, IntroStatus
from app.models.asset import Asset, AssetKind

__all__ = [
    "Base", "utcnow",
    "Role", "Staff",
    "FieldType", "Profession", "ProfessionField",
    "ImportBatch", "Professional", "Stage",
    "Agreement",
    "Lead", "LeadStatus",
    "Event",
    "Review", "ReviewKind",
    "Introduction", "IntroOutcome", "IntroStatus",
    "Asset", "AssetKind",
]
