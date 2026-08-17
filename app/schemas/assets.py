"""Uploaded files — request and response shapes."""
from __future__ import annotations

from datetime import datetime

from app.models import AssetKind
from app.schemas.common import ORMModel


# ── uploaded files ───────────────────────────────────────────────────────────
class AssetOut(ORMModel):
    id: int
    professional_id: int
    kind: AssetKind
    field_key: str | None
    content_type: str | None
    size_bytes: int | None
    original_filename: str | None
    created_at: datetime
    # Deliberately no storage_path: that is where the bytes live, and it is of
    # no use to a client that must read them back through /api/assets/{id}.
