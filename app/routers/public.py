"""What the public website may read.

Deliberately a separate router with its own response models rather than reusing
the admin ones. The admin serialisers carry a professional's direct phone
number, their notes, their pipeline stage and who is calling them — none of
which belongs on a public page. Sharing a schema between the two would mean one
forgotten field is a data leak, so there is no shared schema to forget.

Two rules hold everywhere in this file:
  * only published records are visible, ever
  * a rating is never returned without the source it came from
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.domain.fields import public_values
from app.models import (
    Introduction, IntroOutcome, Profession, Professional, ProfessionField, Review, ReviewKind,
)

router = APIRouter(prefix="/api/public", tags=["public"])


class PublicProfession(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    key: str
    label: str
    plural: str
    hint: str | None = None


class PublicReview(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    author: str
    stars: int
    text: str | None = None
    context: str | None = None
    source: str | None = None
    # Google-sourced and Vilaow-verified reviews must be visibly different on
    # the page. Only the second may be called verified: agreement clause 4
    # ("cannot be bought, edited or removed on request") is enforceable for
    # reviews Vilaow controls, and not for content Google owns.
    kind: str = "google"
    verified: bool = False


class PublicFieldValue(BaseModel):
    """One owner-defined answer the owner marked public."""
    key: str
    label: str
    type: str
    value: object


class PublicCard(BaseModel):
    """The listing card. No phone, no email — a buyer asks for a callback."""
    model_config = ConfigDict(from_attributes=True)
    slug: str
    name: str
    profession: str | None = None
    profession_key: str | None = None
    city: str | None = None
    region: str | None = None
    photo: str | None = None
    initials: str
    rating: float | None = None
    review_count: int | None = None
    rating_source: str | None = None
    years: int | None = None
    languages: list[str] | None = None


class PublicProfile(PublicCard):
    subrole: str | None = None
    coverage: str | None = None
    bio: str | None = None
    specialties: list[str] | None = None
    education: str | None = None
    costs: list | None = None
    cost_note: str | None = None
    faq: list | None = None
    verified_year: int | None = None
    reviews: list[PublicReview] = []
    # Owner-defined answers, already filtered to the public ones. Built from
    # the field definitions rather than from the stored keys, so a value with
    # no public definition behind it cannot appear here however it got saved.
    details: list[PublicFieldValue] = []


def _initials(name: str) -> str:
    parts = [p for p in (name or "").split() if p[:1].isalpha()]
    return "".join(p[0].upper() for p in parts[:2]) or "V"


def _card(p: Professional) -> dict:
    # The public name is the person if we have one, the firm otherwise. His
    # spreadsheet lists businesses, and a buyer wants to know who they will
    # actually speak to.
    name = p.contact_name or p.business_name
    return {
        "slug": p.slug,
        "name": name,
        "profession": p.profession.label if p.profession else None,
        "profession_key": p.profession.key if p.profession else None,
        "city": p.city,
        "region": p.region,
        "photo": p.photo,
        "initials": _initials(name),
        # Attribution travels with the number or neither is returned. Publishing
        # another platform's rating without saying whose it is would be wrong.
        "rating": p.rating if p.source else None,
        "review_count": p.review_count if p.source else None,
        "rating_source": p.source,
        "years": p.years,
        "languages": p.languages,
    }


class PublicStats(BaseModel):
    """The homepage numbers, computed rather than asserted.

    His design ships placeholder copy — "300+ vetted professionals", "2,400+
    foreign purchases supported", "4.8 average verified rating" — against 4
    published profiles and no tracked purchases at all. Publishing those is a
    false claim to consumers, and in the EU that engages the Unfair Commercial
    Practices Directive rather than merely looking silly.

    So every figure here comes from the database, and any figure with nothing
    real behind it comes back as None for the page to omit entirely. A stat
    that cannot be substantiated does not get shown.
    """
    model_config = ConfigDict(from_attributes=True)
    vetted_professionals: int | None = None
    purchases_supported: int | None = None
    average_rating: float | None = None
    # Only true when the average is built from reviews Vilaow itself collected
    # from buyers it introduced. Google's numbers are not ours to call verified.
    rating_is_verified: bool = False
    rating_count: int | None = None


@router.get("/stats", response_model=PublicStats)
def public_stats(db: Session = Depends(get_db)) -> PublicStats:
    professionals = db.scalar(
        select(func.count()).select_from(Professional).where(Professional.published.is_(True))
    ) or 0

    # A purchase we can actually evidence: an introduction a caller closed as
    # the buyer having gone ahead.
    purchases = db.scalar(
        select(func.count()).select_from(Introduction)
        .where(Introduction.outcome == IntroOutcome.buyer_proceeded)
    ) or 0

    verified = db.execute(
        select(func.avg(Review.stars), func.count())
        .where(Review.kind == ReviewKind.vilaow_verified)
    ).one()

    average, count = (float(verified[0]), int(verified[1])) if verified[0] is not None else (None, 0)

    return PublicStats(
        vetted_professionals=professionals or None,
        purchases_supported=purchases or None,
        average_rating=round(average, 1) if average is not None else None,
        rating_is_verified=count > 0,
        rating_count=count or None,
    )


@router.get("/professions", response_model=list[PublicProfession])
def list_professions(db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Profession).where(Profession.active.is_(True)).order_by(Profession.position)
    ).all()
    return [PublicProfession.model_validate(r) for r in rows]


@router.get("/professionals")
def list_professionals(
    region: str | None = None,
    role: str | None = Query(None, description="profession key"),
    city: str | None = None,
    language: str | None = Query(None, description="a language the professional speaks"),
    # Bounded at both ends. Postgres rejects a negative LIMIT, so `?limit=-1`
    # on a public endpoint was an unhandled 500 rather than a 422.
    limit: int = Query(24, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Professional).where(Professional.published.is_(True))
    if region:
        stmt = stmt.where(Professional.region == region)
    if city:
        stmt = stmt.where(Professional.city == city)
    if role:
        stmt = stmt.join(Profession).where(Profession.key == role)
    if language:
        # Region, profession and language are the three filters a buyer gets.
        # Language is a core column rather than an owner-defined field for
        # exactly this reason: it has to work across every profession, so it
        # cannot live in a form the owner might not add to Notary.
        stmt = stmt.where(Professional.languages.any(language))

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Professional.rating.desc().nullslast(), Professional.id)
            .limit(limit).offset(offset)
    ).all()
    return {"total": total, "items": [_card(p) for p in rows]}


@router.get("/professionals/{slug}", response_model=PublicProfile)
def get_professional(slug: str, db: Session = Depends(get_db)):
    p = db.scalar(
        select(Professional).where(
            Professional.slug == slug, Professional.published.is_(True)
        )
    )
    if p is None:
        # 404 whether the record is missing or merely unpublished. Telling the
        # difference would let anyone enumerate who is in the pipeline.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")

    reviews = db.scalars(
        select(Review).where(Review.professional_id == p.id).order_by(Review.created_at.desc())
    ).all()

    # Only the fields the owner marked public, in his display order. Note this
    # reads the *definitions* and looks values up, never the reverse — see
    # app/fields.public_values for why that direction is the safe one.
    fields = db.scalars(
        select(ProfessionField).where(
            ProfessionField.profession_id == p.profession_id,
            ProfessionField.active.is_(True),
            ProfessionField.public.is_(True),
        )
    ).all() if p.profession_id else []

    return PublicProfile(
        **_card(p),
        subrole=p.subrole,
        coverage=p.coverage,
        bio=p.bio,
        specialties=p.specialties,
        education=p.education,
        costs=p.costs,
        cost_note=p.cost_note,
        faq=p.faq,
        verified_year=p.verified_year,
        reviews=[
            PublicReview(
                author=r.author, stars=r.stars, text=r.text, context=r.context,
                source=r.source, kind=r.kind.value,
                verified=r.kind is ReviewKind.vilaow_verified,
            )
            for r in reviews
        ],
        details=[PublicFieldValue(**d) for d in public_values(fields, p.custom)],
    )
