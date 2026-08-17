"""Introductions — request and response shapes."""
from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.models import IntroOutcome, IntroStatus
from app.schemas.common import ORMModel


# ── introductions ────────────────────────────────────────────────────────────
class IntroductionCreate(ORMModel):
    """A stranger asking to be put in touch. No account, so everything here is
    untrusted input and the endpoint is rate limited."""
    slug: str
    buyer_name: str = Field(min_length=1, max_length=160)
    buyer_email: str = Field(min_length=3, max_length=255)
    buyer_phone: str | None = Field(default=None, max_length=60)
    message: str | None = Field(default=None, max_length=2000)
    source_page: str | None = Field(default=None, max_length=255)

    # Submitting this form mails a real person's contact details to a third
    # party. This tick is the lawful basis for that, so the server requires it
    # rather than trusting the page to have enforced it.
    consent: bool = False

    # Honeypot: a field a human never sees and never fills. Anything that
    # arrives with it populated is a bot, and is accepted-then-ignored rather
    # than rejected, so the script gets no signal it was caught.
    website: str | None = None

class IntroductionCreated(ORMModel):
    """Deliberately thin. A public caller learns only that it worked."""
    ok: bool = True
    professional_name: str | None = None

class IntroductionOut(ORMModel):
    id: int
    professional_id: int
    professional_name: str | None
    professional_role: str | None
    city: str | None
    buyer_name: str
    buyer_email: str
    buyer_phone: str | None
    message: str | None
    consent_at: datetime | None
    source_page: str | None
    status: IntroStatus
    outcome: IntroOutcome | None
    assigned_to_id: int | None
    notes: str | None
    created_at: datetime
    due_at: datetime | None
    closed_at: datetime | None
    review_requested_at: datetime | None
    review_submitted_at: datetime | None

class IntroductionListResponse(ORMModel):
    total: int
    items: list[IntroductionOut]

class IntroductionUpdate(ORMModel):
    status: IntroStatus | None = None
    outcome: IntroOutcome | None = None
    notes: str | None = None
    assigned_to_id: int | None = None
