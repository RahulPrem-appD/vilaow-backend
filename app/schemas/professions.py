"""Professions — request and response shapes."""
from __future__ import annotations

from app.schemas.common import ORMModel


# ── professions ──────────────────────────────────────────────────────────────
class ProfessionOut(ORMModel):
    id: int
    key: str
    label: str
    plural: str
    hint: str | None
    position: int
    active: bool

class ProfessionCreate(ORMModel):
    key: str
    label: str
    plural: str
    hint: str | None = None
    position: int = 0
    active: bool = True

class ProfessionUpdate(ORMModel):
    key: str | None = None
    label: str | None = None
    plural: str | None = None
    hint: str | None = None
    position: int | None = None
    active: bool | None = None
