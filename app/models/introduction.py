"""A buyer asking to be put in touch."""
from __future__ import annotations

import enum
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class IntroStatus(str, enum.Enum):
    new = "new"
    chased = "chased"
    closed = "closed"


class IntroOutcome(str, enum.Enum):
    """What actually came of it. The only data that says whether Vilaow works."""
    professional_contacted = "professional_contacted"
    buyer_proceeded = "buyer_proceeded"
    buyer_went_elsewhere = "buyer_went_elsewhere"
    no_response = "no_response"


class Introduction(Base):
    """A stranger asked to be put in touch with a published professional.

    Distinct from Lead, which is a buyer waiting on *Vilaow* to ring back. Here
    the buyer is waiting on the *professional* — but the promise was made in
    Vilaow's name ("Christopher will reach you shortly"), so when it is not kept
    it is Vilaow that broke it. That is why this is a worked queue with an
    overdue clock rather than a log.

    Submitting this form causes a real person's name, email and phone to be
    emailed to a third party, so `consent_at` and `consent_text` record the
    lawful basis for that transfer, captured at the moment they ticked it.
    """
    __tablename__ = "introductions"

    id: Mapped[int] = mapped_column(primary_key=True)
    professional_id: Mapped[int] = mapped_column(ForeignKey("professionals.id"), index=True)
    # Snapshot, like Lead: the queue must still read correctly if the profile
    # is later renamed, unpublished or deleted.
    professional_name: Mapped[str | None] = mapped_column(String(200))
    professional_role: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(80))

    buyer_name: Mapped[str] = mapped_column(String(160))
    buyer_email: Mapped[str] = mapped_column(String(255))
    buyer_phone: Mapped[str | None] = mapped_column(String(60))
    message: Mapped[str | None] = mapped_column(Text)

    consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consent_text: Mapped[str | None] = mapped_column(Text)

    source_page: Mapped[str | None] = mapped_column(String(255))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(Text)

    status: Mapped[IntroStatus] = mapped_column(
        Enum(IntroStatus, name="intro_status"), default=IntroStatus.new, index=True
    )
    outcome: Mapped[IntroOutcome | None] = mapped_column(
        Enum(IntroOutcome, name="intro_outcome"), index=True
    )
    assigned_to_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"), index=True)
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))

    # The review request fires a few days after this closes as buyer_proceeded.
    # The token is what lets a buyer leave a verified review without an account.
    review_token: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    review_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    def set_due(self) -> None:
        """Same 24-hour clock the callback promise uses, for the same reason:
        a promise shown to a buyer belongs in the database, not in a cron job."""
        if self.due_at is None:
            self.due_at = (self.created_at or utcnow()) + timedelta(hours=24)
