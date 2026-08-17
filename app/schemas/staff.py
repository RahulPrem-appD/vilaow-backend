"""Auth / staff — request and response shapes."""
from __future__ import annotations

from datetime import datetime

from app.models import Role
from app.schemas.common import ORMModel


# ── auth / staff ─────────────────────────────────────────────────────────────
class LoginRequest(ORMModel):
    email: str
    password: str

class StaffOut(ORMModel):
    id: int
    name: str
    email: str
    role: Role
    active: bool
    last_active_at: datetime | None
    created_at: datetime

class StaffCreate(ORMModel):
    name: str
    email: str
    password: str
    role: Role = Role.caller
    active: bool = True

class StaffUpdate(ORMModel):
    name: str | None = None
    role: Role | None = None
    active: bool | None = None
