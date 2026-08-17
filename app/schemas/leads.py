"""Leads — request and response shapes."""
from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr, Field

from app.models import LeadStatus
from app.schemas.common import ORMModel


# ── leads ────────────────────────────────────────────────────────────────────
class LeadCreate(ORMModel):
    """The public "call me back" form. Anyone on the internet can post this.

    Every field is bounded, because none of them were: an oversized string
    reached the database and came back as an unhandled 500 to a buyer, and a
    `professional_id` pointing at nothing raised a foreign key error the same
    way. Those are the two shapes of "a stranger can make this endpoint throw".
    """

    professional_id: int | None = Field(default=None, ge=1)
    professional_name: str | None = Field(default=None, max_length=200)
    professional_role: str | None = Field(default=None, max_length=120)
    city: str | None = Field(default=None, max_length=120)

    buyer_name: str = Field(min_length=1, max_length=160)
    buyer_phone: str = Field(min_length=1, max_length=60)
    buyer_email: EmailStr | None = None
    best_time: str | None = Field(default=None, max_length=120)
    message: str | None = Field(default=None, max_length=2000)

    source_page: str | None = Field(default=None, max_length=200)

    # The same honeypot the introduction form uses: a field no human sees, so
    # anything in it came from something filling in every input on the page.
    # Never stored — see the router.
    website: str | None = Field(default=None, exclude=True)

class LeadOut(ORMModel):
    id: int
    professional_id: int | None
    professional_name: str | None
    professional_role: str | None
    city: str | None

    buyer_name: str
    buyer_phone: str
    buyer_email: str | None
    best_time: str | None
    message: str | None

    source_page: str | None
    status: LeadStatus
    created_at: datetime
    callback_due: datetime | None
    contacted_at: datetime | None
    admin_notes: str | None

class LeadUpdate(ORMModel):
    status: LeadStatus | None = None
    admin_notes: str | None = None
