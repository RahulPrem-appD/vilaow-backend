"""HTTP for the pipeline: list, inspect, edit, move, publish.

The rules live in app/services/professionals.py — what publishing requires,
how a custom answer is validated, how a slug is chosen. This file turns a
request into one service call and shapes the reply.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.api.deps import DbDep, ProfessionalServiceDep, StaffDep
from app.models import Event, Review, Stage, Staff
from app.schemas import (
    AssignRequest,
    CallRequest,
    EventOut,
    ProfessionalDetail,
    ProfessionalListResponse,
    ProfessionalOut,
    ProfessionalUpdate,
    ReadinessOut,
    ReviewOut,
    StageChangeRequest,
)
from app.security import current_staff, require_owner
from app.services.professionals import ProfessionalFilters

router = APIRouter(prefix="/api/professionals", tags=["professionals"])


@router.get("", response_model=ProfessionalListResponse)
def list_professionals(
    service: ProfessionalServiceDep,
    _staff: StaffDep,
    stage: Stage | None = None,
    region: str | None = None,
    city: str | None = None,
    profession_id: int | None = None,
    assigned_to_id: int | None = None,
    needs_attention: bool = Query(
        False,
        description="Published profiles missing a required answer for their profession",
    ),
    q: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> ProfessionalListResponse:
    total, items = service.list(
        ProfessionalFilters(
            stage=stage, region=region, city=city, profession_id=profession_id,
            assigned_to_id=assigned_to_id, needs_attention=needs_attention, q=q,
        ),
        limit=limit, offset=offset,
    )
    return ProfessionalListResponse(
        total=total, items=[ProfessionalOut.model_validate(p) for p in items]
    )


@router.get("/{professional_id}", response_model=ProfessionalDetail)
def get_professional(
    professional_id: int, service: ProfessionalServiceDep, db: DbDep, _staff: StaffDep,
) -> ProfessionalDetail:
    professional = service.get(professional_id)
    reviews = db.scalars(
        select(Review).where(Review.professional_id == professional_id)
        .order_by(Review.created_at.desc())
    ).all()
    events = db.scalars(
        select(Event).where(Event.professional_id == professional_id)
        .order_by(Event.created_at.desc())
    ).all()
    base = ProfessionalOut.model_validate(professional)
    return ProfessionalDetail(
        **base.model_dump(),
        reviews=[ReviewOut.model_validate(r) for r in reviews],
        events=[EventOut.model_validate(e) for e in events],
    )


@router.patch("/{professional_id}", response_model=ProfessionalOut)
def update_professional(
    professional_id: int,
    payload: ProfessionalUpdate,
    service: ProfessionalServiceDep,
    _staff: StaffDep,
) -> ProfessionalOut:
    return ProfessionalOut.model_validate(
        service.update(professional_id, payload.model_dump(exclude_unset=True))
    )


@router.get("/{professional_id}/readiness", response_model=ReadinessOut)
def get_readiness(
    professional_id: int, service: ProfessionalServiceDep, _staff: StaffDep,
) -> ReadinessOut:
    """What is still standing between this record and the public site."""
    return ReadinessOut(**service.readiness(professional_id).as_dict())


@router.post("/{professional_id}/stage", response_model=ProfessionalOut)
def change_stage(
    professional_id: int,
    payload: StageChangeRequest,
    service: ProfessionalServiceDep,
    staff: Staff = Depends(current_staff),
) -> ProfessionalOut:
    return ProfessionalOut.model_validate(
        service.change_stage(professional_id, payload.stage, payload.note, staff=staff)
    )


@router.post("/{professional_id}/assign", response_model=ProfessionalOut)
def assign_professional(
    professional_id: int,
    payload: AssignRequest,
    service: ProfessionalServiceDep,
    staff: Staff = Depends(current_staff),
) -> ProfessionalOut:
    return ProfessionalOut.model_validate(
        service.assign(professional_id, payload.assigned_to_id, staff=staff)
    )


@router.post("/{professional_id}/call", response_model=ProfessionalOut)
def record_call(
    professional_id: int,
    payload: CallRequest,
    service: ProfessionalServiceDep,
    staff: Staff = Depends(current_staff),
) -> ProfessionalOut:
    data = payload.model_dump(exclude_unset=True, exclude={"stage"})
    return ProfessionalOut.model_validate(
        service.record_call(
            professional_id, data,
            stage=payload.stage, notes=payload.notes, staff=staff,
        )
    )


@router.post("/{professional_id}/publish", response_model=ProfessionalOut)
def publish_professional(
    professional_id: int,
    service: ProfessionalServiceDep,
    staff: Staff = Depends(require_owner),
) -> ProfessionalOut:
    return ProfessionalOut.model_validate(service.publish(professional_id, staff=staff))


@router.post("/{professional_id}/unpublish", response_model=ProfessionalOut)
def unpublish_professional(
    professional_id: int,
    service: ProfessionalServiceDep,
    staff: Staff = Depends(require_owner),
) -> ProfessionalOut:
    return ProfessionalOut.model_validate(service.unpublish(professional_id, staff=staff))
