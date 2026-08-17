"""The people on the sheet, and where each one sits in the pipeline."""
from __future__ import annotations

from typing import TYPE_CHECKING

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:  # annotations only — relationships resolve by name
    from app.models.profession import Profession
    from app.models.review import Review


class Stage(str, enum.Enum):
    """Where a name sits between "we found them" and "they are on the site".

    not_valid and declined are ends, not stages: a number that never connects
    and a professional who says no both have to go somewhere, or callers keep
    ringing them.
    """
    imported = "imported"
    contacted = "contacted"
    details_collected = "details_collected"
    signature_sent = "signature_sent"
    signed = "signed"
    declined = "declined"
    not_valid = "not_valid"


class Professional(Base):
    __tablename__ = "professionals"
    __table_args__ = (
        # Declared here as well as in the migration, or autogenerate reads the
        # model as the source of truth and proposes dropping it on the next
        # revision anybody generates.
        Index("ix_professionals_custom", "custom", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str | None] = mapped_column(String(140), unique=True, index=True)

    # As imported. His spreadsheet lists businesses ("Atlas Properties Real
    # Estate Agency"), so the person we actually speak to is captured separately
    # on the call rather than overwriting where the name came from.
    business_name: Mapped[str] = mapped_column(String(200))
    contact_name: Mapped[str | None] = mapped_column(String(160))
    phone: Mapped[str | None] = mapped_column(String(60), index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(80), index=True)
    region: Mapped[str | None] = mapped_column(String(80), index=True)

    profession_id: Mapped[int | None] = mapped_column(ForeignKey("professions.id"), index=True)

    # Shown on the profile, exactly as his own pages already do it: stars and
    # a count, attributed. His generator fills {gstars} and {gcount} and his
    # review blocks read "via Google". Attribution is what makes showing another
    # platform's numbers legitimate, so `source` is required reading, not
    # decoration — a rating with no provenance must not be published.
    rating: Mapped[float | None] = mapped_column()
    review_count: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(80))

    stage: Mapped[Stage] = mapped_column(Enum(Stage, name="pipeline_stage"), default=Stage.imported, index=True)
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), index=True)
    called_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"), index=True)

    # His columns, carried over from FIX-add-columns.sql.
    photo: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)
    education: Mapped[str | None] = mapped_column(Text)
    specialties: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    languages: Mapped[list[str] | None] = mapped_column(ARRAY(String))
    years: Mapped[int | None] = mapped_column(Integer)
    license: Mapped[str | None] = mapped_column(String(120))
    vat_number: Mapped[str | None] = mapped_column(String(60))   # ΑΦΜ, from his agreement
    # His FIX-add-columns.sql kept reviews as a JSONB blob on the professional.
    # They are rows here instead: a blob cannot be counted, averaged or ordered
    # without reading every record, and the rating shown on a profile should be
    # derived from the reviews rather than typed in beside them.

    published: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_year: Mapped[int | None] = mapped_column(Integer)

    # The rest of what his profile template renders: {subrole}, {coverage},
    # {cost_html}/{cost_note}, {faq_html}.
    subrole: Mapped[str | None] = mapped_column(String(160))
    coverage: Mapped[str | None] = mapped_column(String(160))
    cost_note: Mapped[str | None] = mapped_column(Text)
    costs: Mapped[list | None] = mapped_column(JSONB, default=list)
    faq: Mapped[list | None] = mapped_column(JSONB, default=list)

    # Answers to this profession's own fields, keyed by ProfessionField.key.
    # Held as one document rather than a row per answer: the values are only
    # ever read together, for one professional at a time, and a JSONB column
    # with a GIN index still filters faster than the join would.
    #
    # Nothing in here is public by default. The public serialiser looks up each
    # key's field definition and emits it only when that field is `public`, so
    # a value can never reach a stranger just because a caller typed it.
    custom: Mapped[dict | None] = mapped_column(JSONB, default=dict)

    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profession: Mapped[Profession | None] = relationship()
    reviews_rel: Mapped[list["Review"]] = relationship(cascade="all, delete-orphan")


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    filename: Mapped[str] = mapped_column(String(255))
    imported_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    rows_seen: Mapped[int] = mapped_column(Integer, default=0)
    rows_added: Mapped[int] = mapped_column(Integer, default=0)
    rows_skipped: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
