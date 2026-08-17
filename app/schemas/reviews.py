"""Reviews / events (read-only, embedded in professional detail) — request and response shapes."""
from __future__ import annotations

from datetime import datetime

from app.models import ReviewKind
from app.schemas.common import ORMModel


# ── reviews / events (read-only, embedded in professional detail) ───────────
class ReviewOut(ORMModel):
    id: int
    professional_id: int
    author: str
    stars: int
    text: str | None
    context: str | None
    source: str | None
    # Shipped because the two kinds mean genuinely different things and were
    # rendering identically. A `vilaow_verified` review is from a buyer Vilaow
    # can show it introduced and who confirmed the work went ahead; a `google`
    # one is a rating copied from another platform with no such proof. The
    # review page tells a buyer "it will show as verified" — this is what makes
    # that true.
    kind: ReviewKind
    created_at: datetime

class EventOut(ORMModel):
    id: int
    professional_id: int | None
    lead_id: int | None
    actor_id: int | None
    actor_label: str | None
    kind: str
    detail: str | None
    created_at: datetime
