"""HTTP for introductions and the reviews that follow them.

Two routers because there are two audiences: a stranger with no account, and a
caller with a session. The rules — consent, rate limiting, the honeypot, what
closing requires — live in app/services/introductions.py. This file reads
requests and shapes responses.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select

from app.api.clients import client_ip
from app.api.deps import (
    DbDep,
    IntroductionServiceDep,
    SettingsDep,
    StaffDep,
    VerifiedReviewServiceDep,
)
from app.models import Introduction, IntroOutcome, IntroStatus, Staff
from app.schemas import (
    IntroductionCreate,
    IntroductionCreated,
    IntroductionListResponse,
    IntroductionOut,
    IntroductionUpdate,
    VerifiedReviewContext,
    VerifiedReviewCreate,
)
from app.security import current_staff, require_owner
from app.services.introductions import IntroductionRequest, short_author

public_router = APIRouter(prefix="/api/public", tags=["public"])
router = APIRouter(prefix="/api/introductions", tags=["introductions"])


@public_router.post("/introductions", response_model=IntroductionCreated,
                    status_code=status.HTTP_201_CREATED)
def request_introduction(
    payload: IntroductionCreate, request: Request, service: IntroductionServiceDep,
    settings: SettingsDep,
) -> IntroductionCreated:
    intro = service.request(IntroductionRequest(
        slug=payload.slug,
        buyer_name=payload.buyer_name,
        buyer_email=payload.buyer_email,
        buyer_phone=payload.buyer_phone,
        message=payload.message,
        source_page=payload.source_page,
        consent=payload.consent,
        honeypot=payload.website,
        ip=client_ip(request, settings),
        user_agent=request.headers.get("user-agent"),
    ))
    # `None` is the honeypot: answer exactly as if it worked.
    return IntroductionCreated(
        professional_name=intro.professional_name if intro else None
    )


@router.get("", response_model=IntroductionListResponse)
def list_introductions(
    db: DbDep,
    _staff: StaffDep,
    status_filter: IntroStatus | None = Query(None, alias="status"),
    outcome: IntroOutcome | None = None,
    overdue: bool = False,
    assigned_to_id: int | None = None,
    professional_id: int | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
) -> IntroductionListResponse:
    stmt = select(Introduction)
    if status_filter is not None:
        stmt = stmt.where(Introduction.status == status_filter)
    if outcome is not None:
        stmt = stmt.where(Introduction.outcome == outcome)
    if assigned_to_id is not None:
        stmt = stmt.where(Introduction.assigned_to_id == assigned_to_id)
    if professional_id is not None:
        stmt = stmt.where(Introduction.professional_id == professional_id)
    if overdue:
        # Overdue means a promise is being broken right now: past due and still
        # open. A closed one cannot be overdue however old it is.
        stmt = stmt.where(
            Introduction.due_at < func.now(),
            Introduction.status != IntroStatus.closed,
        )

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Introduction.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return IntroductionListResponse(
        total=total, items=[IntroductionOut.model_validate(r) for r in rows]
    )


@router.get("/{introduction_id}", response_model=IntroductionOut)
def get_introduction(
    introduction_id: int, service: IntroductionServiceDep, _staff: StaffDep,
) -> IntroductionOut:
    return IntroductionOut.model_validate(service.get(introduction_id))


@router.patch("/{introduction_id}", response_model=IntroductionOut)
def update_introduction(
    introduction_id: int,
    payload: IntroductionUpdate,
    service: IntroductionServiceDep,
    staff: Staff = Depends(current_staff),
) -> IntroductionOut:
    changes = payload.model_dump(exclude_unset=True)
    return IntroductionOut.model_validate(
        service.update(introduction_id, changes, staff=staff)
    )


@router.delete("/{introduction_id}", status_code=status.HTTP_204_NO_CONTENT)
def erase_introduction(
    introduction_id: int,
    service: IntroductionServiceDep,
    staff: Staff = Depends(require_owner),
) -> None:
    service.erase(introduction_id, staff=staff)


@router.post("/send-review-requests")
def send_due_review_requests(
    service: IntroductionServiceDep, _staff: StaffDep,
) -> dict:
    return service.send_due_review_requests()


# ── the verified review ─────────────────────────────────────────────────────
@public_router.get("/reviews/{token}", response_model=VerifiedReviewContext)
def review_context(token: str, service: VerifiedReviewServiceDep) -> VerifiedReviewContext:
    intro = service.context(token)
    return VerifiedReviewContext(
        professional_name=intro.professional_name or "",
        professional_role=intro.professional_role,
        city=intro.city,
        already_submitted=intro.review_submitted_at is not None,
        display_name=short_author(intro.buyer_name),
    )


@public_router.post("/reviews/{token}", status_code=status.HTTP_201_CREATED)
def submit_verified_review(
    token: str, payload: VerifiedReviewCreate, service: VerifiedReviewServiceDep,
) -> dict:
    service.submit(token, stars=payload.stars, text=payload.text)
    return {"ok": True}
