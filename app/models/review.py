"""Ratings and reviews, with their provenance."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ReviewKind(str, enum.Enum):
    """Where a review came from, and therefore what may be claimed about it.

    These must never be blurred together on a profile. Vilaow controls its own
    reviews, so clause 4 — "cannot be bought, edited or removed on request" —
    is enforceable for `vilaow_verified`. It is not enforceable for Google
    content, which Google owns and can change at any time. Only a
    `vilaow_verified` review may be described as verified.
    """
    google = "google"                    # copied from a public listing by a caller
    vilaow_verified = "vilaow_verified"  # from a buyer Vilaow actually introduced


class Review(Base):
    """A buyer review, shown on the profile the way his pages already show them:
    name, stars, words, when, and where it came from.

    His agreement commits to this in writing — reviews "cannot be bought, edited
    or removed on request" — so there is deliberately no edit path here, and
    `source` records provenance ("via Google", "Vilaow buyer") rather than
    leaving a number floating without attribution.
    """
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    professional_id: Mapped[int] = mapped_column(ForeignKey("professionals.id", ondelete="CASCADE"), index=True)
    kind: Mapped[ReviewKind] = mapped_column(
        Enum(ReviewKind, name="review_kind"), default=ReviewKind.google, index=True
    )
    # Set only for vilaow_verified: the introduction this review came out of.
    # It is what makes "verified" true — the reviewer is a buyer we can show we
    # introduced, not an anonymous submission.
    introduction_id: Mapped[int | None] = mapped_column(
        ForeignKey("introductions.id", ondelete="SET NULL"), index=True
    )
    author: Mapped[str] = mapped_column(String(120))
    stars: Mapped[int] = mapped_column(Integer)
    text: Mapped[str | None] = mapped_column(Text)
    context: Mapped[str | None] = mapped_column(String(160))   # "Completed purchase, May 2024"
    source: Mapped[str | None] = mapped_column(String(80))     # "via Google"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
