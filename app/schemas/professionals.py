"""Professionals — request and response shapes."""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Any

from pydantic import Field, field_validator

from app.models import Stage
from app.schemas.common import ORMModel
from app.schemas.professions import ProfessionOut
from app.schemas.reviews import EventOut, ReviewOut


# ── professionals ────────────────────────────────────────────────────────────

def json_storable(value: Any) -> Any:
    """Refuse the two numbers `json.loads` accepts and JSONB does not.

    FastAPI parses request bodies with `json.loads`, which takes the
    non-standard `NaN`, `Infinity` and `-Infinity` literals. `list[Any]`
    imposes nothing, so they reached the JSONB column and failed on insert as
    a 500 — the same failure `_finite` prevents for owner-defined number
    fields, on the two columns that were never routed through it.
    """
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("must be a finite number")
    if isinstance(value, list):
        return [json_storable(item) for item in value]
    if isinstance(value, dict):
        return {key: json_storable(item) for key, item in value.items()}
    return value


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
    rating_captured_on: date | None

    stage: Stage
    assigned_to_id: int | None
    called_by_id: int | None
    called_at: datetime | None
    batch_id: int | None

    photo: str | None
    bio: str | None
    education: str | None
    specialties: list[str] | None
    highlights: list[str] | None
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

    # Which fields this record's public page shows, as keys from
    # app/domain/visibility.py — null means inherit the profession's list.
    # Staff-facing like the rest of this schema: the public serialiser reads it
    # to decide what to withhold, never to publish something the record never
    # showed before.
    visible_fields: list[str] | None

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
    edit.

    `rating_captured_on` is the one exception, and it is here because it is not
    an imported value at all: it is a caller saying when they checked the
    listing. The public page will not publish the imported figure without it,
    so a caller who has just looked at Google needs a way to say so. Clearing
    it back to null withdraws the Google figure and returns the page to the
    rating computed from review rows.
    """

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
    highlights: list[str] | None = None
    languages: list[str] | None = None
    years: int | None = None
    license: str | None = None
    vat_number: str | None = None
    verified_year: int | None = None
    rating_captured_on: date | None = None

    subrole: str | None = None
    coverage: str | None = None
    cost_note: str | None = None
    costs: list[Any] | None = None
    faq: list[Any] | None = None

    # Which of the public fields this professional's page shows, as keys from
    # app/domain/visibility.py. Validated against that closed vocabulary by
    # the service rather than accepted as free text, so an unknown key is a
    # 422 naming it instead of a silent no-op. Null goes back to inheriting
    # the profession's list; an empty list is a real answer — show nothing.
    visible_fields: list[str] | None = None

    # Answers to the profession's own fields, keyed by field key. Merged over
    # what is already stored rather than replacing it, so a form that submits
    # one field does not silently wipe the rest. Validated against the field
    # definitions before anything is written.
    custom: dict[str, Any] | None = None

    # `costs`, `faq` and `custom` are the three free-shaped values on this
    # schema that reach a JSONB column. A validator rather than a type, because
    # the shapes are genuinely open — what has to be closed is the one value
    # Postgres will not store.
    @field_validator("costs", "faq", "custom")
    @classmethod
    def _finite_json(cls, value: Any) -> Any:
        return json_storable(value)

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

class GoogleReviewCreate(ORMModel):
    """A rating a caller copies from the professional's public listing.

    There is deliberately no edit shape to match this one: a wrong Google
    review is deleted and retyped, which keeps the audit trail honest about
    what was on the record and when.
    """

    author: str = Field(min_length=1, max_length=120)
    stars: int = Field(ge=1, le=5)
    text: str | None = None
    # "Pre-purchase survey, May 2024" — what the buyer was going through, so
    # the stars have a situation attached rather than floating alone.
    context: str | None = Field(default=None, max_length=160)
