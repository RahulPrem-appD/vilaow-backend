"""Buyers asking to be called back."""
from __future__ import annotations

import enum
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class LeadStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    closed = "closed"
    lost = "lost"


class Lead(Base):
    """His table, kept. The 24-hour callback is promised to buyers on the site,
    so callback_due is set on insert rather than computed at read time."""
    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(primary_key=True)
    professional_id: Mapped[int | None] = mapped_column(ForeignKey("professionals.id"), index=True)
    professional_name: Mapped[str | None] = mapped_column(String(200))
    professional_role: Mapped[str | None] = mapped_column(String(80))
    city: Mapped[str | None] = mapped_column(String(80))

    buyer_name: Mapped[str] = mapped_column(String(160))
    buyer_phone: Mapped[str] = mapped_column(String(60))
    buyer_email: Mapped[str | None] = mapped_column(String(255))
    best_time: Mapped[str | None] = mapped_column(String(80))
    message: Mapped[str | None] = mapped_column(Text)

    source_page: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[LeadStatus] = mapped_column(Enum(LeadStatus, name="lead_status"), default=LeadStatus.new, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    callback_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    admin_notes: Mapped[str | None] = mapped_column(Text)

    def set_callback_due(self) -> None:
        if self.callback_due is None:
            self.callback_due = (self.created_at or utcnow()) + timedelta(hours=24)
