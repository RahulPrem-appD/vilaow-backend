"""Reviews left by a buyer we introduced — request and response shapes."""
from __future__ import annotations

from pydantic import Field

from app.schemas.common import ORMModel


# ── reviews left by a buyer we introduced ───────────────────────────────────
class VerifiedReviewContext(ORMModel):
    """What the review page shows before a buyer writes anything."""
    professional_name: str
    professional_role: str | None = None
    city: str | None = None
    already_submitted: bool = False
    # Exactly what will appear under the review, computed the same way it is
    # stored. The buyer's real name was shortened to "Sarah M." and published
    # on a public profile permanently — the shortening is deliberate, but
    # nothing told them it was happening, on a form whose own copy says the
    # review cannot be removed on request.
    display_name: str = "A buyer"

class VerifiedReviewCreate(ORMModel):
    stars: int = Field(ge=1, le=5)
    text: str | None = Field(default=None, max_length=2000)
