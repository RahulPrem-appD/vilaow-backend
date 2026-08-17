"""Uploaded files."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AssetKind(str, enum.Enum):
    photo = "photo"        # the profile photo buyers see
    document = "document"  # licence scan, indemnity insurance — never public


class Asset(Base):
    """A file in object storage, with the row that says what it is.

    The bytes live in Firebase Storage; this table holds the pointer and the
    provenance. Documents are owner-only on download: a caller can see that a
    licence is attached and verified, but cannot pull the file. Same fail-closed
    principle as internal fields.

    Nothing here expires on its own — retention is manual deletion by design —
    so `deleted_at` exists to make erasure a real, auditable action rather than
    a row vanishing.
    """
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    professional_id: Mapped[int] = mapped_column(
        ForeignKey("professionals.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[AssetKind] = mapped_column(Enum(AssetKind, name="asset_kind"), index=True)
    # Set when this file is the answer to a `file` field on the profession.
    field_key: Mapped[str | None] = mapped_column(String(60), index=True)

    storage_path: Mapped[str] = mapped_column(String(500))
    content_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    original_filename: Mapped[str | None] = mapped_column(String(255))

    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
