"""The owner-defined form — request and response shapes."""
from __future__ import annotations

from typing import Any

from pydantic import Field

from app.models import FieldType
from app.schemas.common import ORMModel


# ── the owner-defined form ──────────────────────────────────────────────────
class ProfessionFieldOut(ORMModel):
    id: int
    profession_id: int
    key: str
    label: str
    help_text: str | None
    type: FieldType
    options: list[Any] | None
    required: bool
    public: bool
    position: int
    active: bool

class ProfessionFieldCreate(ORMModel):
    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1, max_length=120)
    help_text: str | None = Field(default=None, max_length=240)
    type: FieldType
    options: list[str] = Field(default_factory=list)
    required: bool = False
    # Internal unless the owner deliberately says otherwise. The default is the
    # safety property: a field added without thinking is never public.
    public: bool = False
    position: int = 0
    active: bool = True

class ProfessionFieldUpdate(ORMModel):
    label: str | None = Field(default=None, min_length=1, max_length=120)
    help_text: str | None = Field(default=None, max_length=240)
    options: list[str] | None = None
    required: bool | None = None
    public: bool | None = None
    position: int | None = None
    active: bool | None = None

class PublicFieldValue(ORMModel):
    """One owner-defined answer, as shown on a public profile."""
    key: str
    label: str
    type: str
    value: Any
