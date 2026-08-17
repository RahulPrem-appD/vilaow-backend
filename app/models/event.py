"""The append-only audit trail."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Event(Base):
    """The record history the admin shows: imported, called, sent, signed,
    published. Append-only; nothing here is ever edited."""
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    professional_id: Mapped[int | None] = mapped_column(ForeignKey("professionals.id", ondelete="CASCADE"), index=True)
    lead_id: Mapped[int | None] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    actor_label: Mapped[str | None] = mapped_column(String(160))  # survives a deleted staff row
    kind: Mapped[str] = mapped_column(String(60), index=True)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
