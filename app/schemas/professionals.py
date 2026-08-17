"""Professionals — request and response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import Field

from app.models import Stage
from app.schemas.common import ORMModel
from app.schemas.professions import ProfessionOut
from app.schemas.reviews import EventOut, ReviewOut


# ── professionals ────────────────────────────────────────────────────────────
class ProfessionalOut(ORMModel):
    id: int
    slug: str | None
    business_name: str
    contact_name: str | None
    phone: str | None
    email: str | None
    address: str | None
    city: str | None
    region: str | None
    profession_id: int | None
    profession: ProfessionOut | None = None

    rating: float | None
    review_count: int | None
    source: str | None

    stage: Stage
    assigned_to_id: int | None
    called_by_id: int | None
    called_at: datetime | None
    batch_id: int | None

    photo: str | None
    bio: str | None
    education: str | None
    specialties: list[str] | None
    languages: list[str] | None
    years: int | None
    license: str | None
    vat_number: str | None

    published: bool
    published_at: datetime | None
    verified_year: int | None

    subrole: str | None
    coverage: str | None
    cost_note: str | None
    costs: list[Any] | None
    faq: list[Any] | None

    # Raw answers, keyed by field key. Admin-only — this is the staff schema.
    # The public serialiser never touches this; it rebuilds a filtered view
    # from the field definitions instead. See app/fields.public_values.
    custom: dict[str, Any] | None = None

    notes: str | None
    created_at: datetime

class ProfessionalDetail(ProfessionalOut):
    reviews: list[ReviewOut] = Field(default_factory=list)
    events: list[EventOut] = Field(default_factory=list)

class ProfessionalListResponse(ORMModel):
    total: int
    items: list[ProfessionalOut]

class ProfessionalUpdate(ORMModel):
    """Editable profile fields. Stage, assignment, publish state and the call
    stamp all have dedicated endpoints and are deliberately absent here, as
    are rating/review_count/source — those come from the import, not a staff
    edit."""

    business_name: str | None = None
    contact_name: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    city: str | None = None
    region: str | None = None
    profession_id: int | None = None

    photo: str | None = None
    bio: str | None = None
    education: str | None = None
    specialties: list[str] | None = None
    languages: list[str] | None = None
    years: int | None = None
    license: str | None = None
    vat_number: str | None = None
    verified_year: int | None = None

    subrole: str | None = None
    coverage: str | None = None
    cost_note: str | None = None
    costs: list[Any] | None = None
    faq: list[Any] | None = None

    # Answers to the profession's own fields, keyed by field key. Merged over
    # what is already stored rather than replacing it, so a form that submits
    # one field does not silently wipe the rest. Validated against the field
    # definitions before anything is written.
    custom: dict[str, Any] | None = None

    notes: str | None = None

class ReadinessOut(ORMModel):
    """Why a profile can or cannot go live. Computed server-side so the badge
    and the publish endpoint can never disagree."""
    ready: bool
    blockers: list[str]
    missing_field_keys: list[str]

class StageChangeRequest(ORMModel):
    stage: Stage
    note: str | None = None

class AssignRequest(ORMModel):
    assigned_to_id: int

class CallRequest(ProfessionalUpdate):
    """Everything ProfessionalUpdate allows, plus the call stamp. `stage`
    overrides the default post-call stage (details_collected) when given."""

    stage: Stage | None = None
