"""Every WhatsApp message to and from Vilaow's number, for the staff inbox.

The follow-up's steps (introduction_step.py) say what each message did for an
introduction. This is the conversation itself, one row per message, both ways:
what people wrote to Vilaow's number, what staff and the assistant answered,
and the templates the follow-up sent. Staff read it at /admin/whatsapp.

Meta's rule shapes it: free text can only go to someone who wrote in the last
24 hours, so a person's last inbound message decides what staff may send them
(app/services/whatsapp_inbox.py).

A message that belongs to an introduction goes with it (ON DELETE CASCADE), as
its steps do. The owner can also erase a whole conversation, from the inbox.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class InboxMessage(Base):
    __tablename__ = "whatsapp_messages"
    __table_args__ = (
        # One conversation, in order: what the inbox and the 24-hour rule read.
        Index("ix_whatsapp_messages_number_at", "number", "at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # The other person's number, E.164: who wrote, or who it went to.
    number: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(3))    # "in" or "out"
    # "text", "button" or "template", or what WhatsApp calls anything else:
    # "image", "audio", "reaction", …
    kind: Mapped[str] = mapped_column(String(16))
    # The words: what was typed, a button's label, a photo's caption, or a
    # template as it read.
    body: Mapped[str | None] = mapped_column(Text)
    # Our key, for a template: "thank_buyer", …
    template: Mapped[str | None] = mapped_column(String(40))
    # In: "received". Out: "sent", "delivered", "read", "failed", or "pretend"
    # when nothing was really sent.
    status: Mapped[str] = mapped_column(String(12))
    # Why it failed.
    detail: Mapped[str | None] = mapped_column(Text)
    # Meta's id for the message. Unique, so a callback Meta delivers twice is
    # stored once.
    provider_id: Mapped[str | None] = mapped_column(String(128), unique=True)
    # Out only: "staff", "assistant" or "follow-up".
    sent_by: Mapped[str | None] = mapped_column(String(12))
    staff_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    introduction_id: Mapped[int | None] = mapped_column(
        ForeignKey("introductions.id", ondelete="CASCADE"), index=True
    )
    # In only: the name the person goes by on WhatsApp, as Meta passes it on.
    profile_name: Mapped[str | None] = mapped_column(String(120))
    # When it was written. For an inbound message, WhatsApp's own time: a
    # callback Meta retries can arrive late, and the 24 hours run from this.
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # In only: when staff opened the conversation. Null is unread.
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # In only: when staff were emailed about it.
    alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
