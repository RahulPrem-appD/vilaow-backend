"""Gathering the facts the publish gate needs.

The rule itself is pure (app/domain/publishing.py). This is the half that
touches the database, kept separate so the rule can be unit tested with a
literal `PublishFacts` and no session at all.

A repository earns its place here because the query is not a `db.get()`: it
joins the agreement state, the profession's active field definitions and the
stored answers into one shape. Where a lookup really is just a primary key,
services use the Session directly — wrapping that in a repository would be
ceremony, not separation.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.publishing import PublishFacts, Readiness, evaluate, needs_attention
from app.models import Agreement, Professional, ProfessionField


def profession_fields(db: Session, profession_id: int | None) -> list[ProfessionField]:
    if profession_id is None:
        return []
    return list(db.scalars(
        select(ProfessionField)
        .where(ProfessionField.profession_id == profession_id,
               ProfessionField.active.is_(True))
        .order_by(ProfessionField.position, ProfessionField.label)
    ).all())


def signed_agreement(db: Session, professional_id: int) -> Agreement | None:
    """The most recent fully completed agreement — signed *and* confirmed."""
    return db.scalar(
        select(Agreement)
        .where(Agreement.professional_id == professional_id,
               Agreement.signed_at.is_not(None),
               Agreement.email_verified_at.is_not(None))
        .order_by(Agreement.signed_at.desc())
    )


def facts_for(db: Session, professional: Professional) -> PublishFacts:
    any_signed = db.scalar(
        select(Agreement)
        .where(Agreement.professional_id == professional.id,
               Agreement.signed_at.is_not(None))
        .order_by(Agreement.signed_at.desc())
    )
    # Deliberately "does a completed agreement exist", not "is the most recent
    # one completed". Reading `email_confirmed` off the latest row meant a
    # second agreement — issued by an owner for any reason and then abandoned
    # at the confirmation step — shadowed a perfectly good first one, and the
    # profile could never be published again despite valid consent on file.
    # `signed_agreement()` above already asks the right question; the gate
    # simply was not using it.
    completed = signed_agreement(db, professional.id)
    return PublishFacts(
        has_profession=professional.profession_id is not None,
        agreement_signed=any_signed is not None,
        email_confirmed=completed is not None,
        has_photo=bool(professional.photo),
        required_fields=tuple(profession_fields(db, professional.profession_id)),
        answers=professional.custom or {},
    )


def readiness(db: Session, professional: Professional) -> Readiness:
    return evaluate(facts_for(db, professional))


def attention_keys(db: Session, professional: Professional) -> list[str]:
    return needs_attention(
        professional.published,
        profession_fields(db, professional.profession_id),
        professional.custom,
    )
