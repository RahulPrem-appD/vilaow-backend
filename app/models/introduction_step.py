"""The WhatsApp follow-up of an introduction, one row per step.

Change request 12: when a buyer asks to be put in touch, Vilaow thanks them,
sends their details to the professional, reminds him until he says he made
contact, and checks with the buyer two days later — "every step saved on the
lead with its time, so we can see what happened".

A row is both the plan and the record. Every message is written here as
`scheduled` with the time it is due the moment the introduction arrives, and
the same row then says what became of it: sent, delivered, read, failed, not
needed any more, or skipped because there was no number to send to. A reply is
a row of its own. So the admin reads one list, in order, and sees the future
steps as well as the past ones.

Rows go with the introduction (ON DELETE CASCADE): they hold the buyer's
number and anything they typed, and erasing an enquiry must erase those too.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

# Strings rather than Postgres enums, so a new kind of step is a code change and
# not a migration. The service only ever writes these values.
STEP_KINDS = (
    "thank_buyer",      # to the buyer, straight away
    "request_to_pro",   # the buyer's details, to the professional, straight away
    "remind_pro",       # "did you contact the client?", until he says yes
    "check_buyer",      # "has the professional contacted you?", after two days
    "pro_replied",      # what the professional answered
    "buyer_replied",    # what the buyer answered
)
STEP_STATUSES = (
    "scheduled", "sent", "delivered", "read", "failed", "cancelled", "skipped", "received",
)


class IntroductionStep(Base):
    __tablename__ = "introduction_steps"
    __table_args__ = (
        # What the clock asks every few minutes: which scheduled steps are due.
        Index("ix_introduction_steps_due", "status", "due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    introduction_id: Mapped[int] = mapped_column(
        ForeignKey("introductions.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(20))
    # Who it went to — or, for a reply, who it came from.
    recipient: Mapped[str] = mapped_column(String(12))
    status: Mapped[str] = mapped_column(String(12))
    # Which reminder, from 1. Null for every other kind.
    attempt: Mapped[int | None] = mapped_column(Integer)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When it reached its status. Null while it is scheduled.
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Sending attempts. A provider hiccup is retried a few times, then it fails.
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # The number it went to, or came from, as E.164. Kept on the step because
    # the professional's record can change after the message went out, and a
    # reply is matched to the number the question was really sent to.
    number: Mapped[str | None] = mapped_column(String(20), index=True)
    # "pretend" while WhatsApp is not connected: written down, never sent.
    transport: Mapped[str | None] = mapped_column(String(10))
    # The provider's id for the message, which is how a reply or a delivery
    # report finds its way back to the step. Unique, so a reply Twilio
    # delivers twice at the same moment is stored once, not acted on twice.
    provider_id: Mapped[str | None] = mapped_column(String(64), unique=True, index=True)
    # On a reply: "yes", "no", or "other" for a typed message.
    answer: Mapped[str | None] = mapped_column(String(8))
    # Why it failed, was skipped or was not needed — or a typed reply's words.
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
