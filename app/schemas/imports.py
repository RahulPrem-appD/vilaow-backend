"""Imports — request and response shapes."""
from __future__ import annotations

from datetime import datetime

from app.schemas.common import ORMModel


# ── imports ──────────────────────────────────────────────────────────────────
class ImportBatchOut(ORMModel):
    id: int
    filename: str
    imported_by_id: int | None
    rows_seen: int
    rows_added: int
    rows_skipped: int
    note: str | None
    created_at: datetime

class ImportSkipBreakdown(ORMModel):
    no_phone: int
    unknown_category: int
    unmapped_profession: int
    duplicate_existing: int
    duplicate_batch: int

class ImportSummary(ORMModel):
    batch: ImportBatchOut
    skipped: ImportSkipBreakdown
