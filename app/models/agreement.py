"""What was signed, and which words were signed."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Agreement(Base):
    """What was signed, and which words were signed.

    His agreement is eight numbered clauses that declare themselves "valid and
    binding" under Greek law, so this row has to be evidence, not a flag. The
    version alone is not enough — `terms_text` keeps the clauses **verbatim**,
    so a PDF regenerated years later still reproduces the words that were on
    screen, even if the template has since been rewritten.

    The PDF is rendered on demand from this row rather than stored as a file.
    """
    __tablename__ = "agreements"

    id: Mapped[int] = mapped_column(primary_key=True)
    professional_id: Mapped[int] = mapped_column(ForeignKey("professionals.id", ondelete="CASCADE"), index=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    terms_version: Mapped[str] = mapped_column(String(20))
    terms_text: Mapped[str | None] = mapped_column(Text)

    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    signed_name: Mapped[str | None] = mapped_column(String(160))
    signed_email: Mapped[str | None] = mapped_column(String(255))
    # The drawn signature, as the PNG data URL the canvas produces. Kept in the
    # database rather than object storage: it is a few kilobytes, it is the
    # evidence, and it should not become unreadable because a bucket moved.
    signature_image: Mapped[str | None] = mapped_column(Text)
    # Every field value exactly as submitted. The professional can correct what
    # a caller pre-filled, and their version is what they attested to — so it is
    # frozen here as well as written back to the record.
    signed_fields: Mapped[dict | None] = mapped_column(JSONB, default=dict)
    signed_ip: Mapped[str | None] = mapped_column(String(64))
    signed_user_agent: Mapped[str | None] = mapped_column(Text)

    # Email verification, not a second factor: the code goes to the same inbox
    # as the signing link, so anyone who can open the link can read it. It is
    # worth having because it proves the address is real and reachable. Never
    # describe it as security.
    otp_hash: Mapped[str | None] = mapped_column(String(255))
    otp_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    otp_attempts: Mapped[int] = mapped_column(Integer, default=0)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
