"""Professions, and the form the owner builds for each one."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Profession(Base):
    """Editable by the owner, so adding "surveyor" is not a code change."""
    __tablename__ = "professions"

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(80))
    plural: Mapped[str] = mapped_column(String(80))
    hint: Mapped[str | None] = mapped_column(String(160))
    position: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

    fields: Mapped[list["ProfessionField"]] = relationship(
        cascade="all, delete-orphan",
        order_by="ProfessionField.position",
    )


class FieldType(str, enum.Enum):
    """What a caller is asked to put in the box.

    Deliberately a closed list. A generic "any JSON" field would be easier to
    build and impossible to validate, filter or render consistently.
    """
    short_text = "short_text"      # licence number, VAT / ΑΦΜ, firm name
    long_text = "long_text"        # the bio paragraph
    select = "select"              # one of a fixed list
    multi_select = "multi_select"  # several of a fixed list
    number = "number"              # years qualified, typical fee %
    date = "date"                  # qualified since, insurance expiry
    file = "file"                  # licence scan, indemnity insurance


class ProfessionField(Base):
    """A question the owner decided this profession has to answer.

    A lawyer needs a bar registration number; a contractor does not. Rather than
    a column per trade, the owner builds the form once per profession and every
    professional of that profession is captured against it.

    `public` is the load-bearing flag. The public API is a separate layer with
    its own schemas precisely so that internal data cannot leak, and dynamic
    fields are the one thing that could punch a hole in that. So it defaults to
    False: a field is only ever visible to strangers if the owner deliberately
    ticks it. Fail closed.
    """
    __tablename__ = "profession_fields"
    __table_args__ = (UniqueConstraint("profession_id", "key", name="uq_profession_field_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    profession_id: Mapped[int] = mapped_column(
        ForeignKey("professions.id", ondelete="CASCADE"), index=True
    )

    key: Mapped[str] = mapped_column(String(60))
    label: Mapped[str] = mapped_column(String(120))
    help_text: Mapped[str | None] = mapped_column(String(240))
    type: Mapped[FieldType] = mapped_column(Enum(FieldType, name="profession_field_type"))

    # Only meaningful for select / multi_select: the allowed answers. A fixed
    # option list is what makes a field filterable; free text is not.
    options: Mapped[list | None] = mapped_column(JSONB, default=list)

    # Blocks publication when empty. Adding one to a live profession never
    # unpublishes anyone — it raises a "needs attention" backlog instead.
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    public: Mapped[bool] = mapped_column(Boolean, default=False)

    position: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
