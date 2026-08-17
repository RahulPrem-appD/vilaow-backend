"""HTTP for professions and their forms.

Any staff member may read; only an owner may write. That is expressed here, in
the dependency each route asks for, because it is a question about the caller
rather than about the rule — the rules themselves are in
app/services/professions.py.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.api.deps import ProfessionServiceDep, StaffDep
from app.models import Staff
from app.schemas import (
    ProfessionCreate,
    ProfessionFieldCreate,
    ProfessionFieldOut,
    ProfessionFieldUpdate,
    ProfessionOut,
    ProfessionUpdate,
)
from app.security import require_owner

router = APIRouter(prefix="/api/professions", tags=["professions"])


@router.get("", response_model=list[ProfessionOut])
def list_professions(service: ProfessionServiceDep, _staff: StaffDep) -> list[ProfessionOut]:
    return [ProfessionOut.model_validate(p) for p in service.list()]


@router.post("", response_model=ProfessionOut, status_code=status.HTTP_201_CREATED)
def create_profession(
    payload: ProfessionCreate,
    service: ProfessionServiceDep,
    _staff: Staff = Depends(require_owner),
) -> ProfessionOut:
    return ProfessionOut.model_validate(service.create(payload.model_dump()))


@router.patch("/{profession_id}", response_model=ProfessionOut)
def update_profession(
    profession_id: int,
    payload: ProfessionUpdate,
    service: ProfessionServiceDep,
    _staff: Staff = Depends(require_owner),
) -> ProfessionOut:
    return ProfessionOut.model_validate(
        service.update(profession_id, payload.model_dump(exclude_unset=True))
    )


# ── the owner-defined form ──────────────────────────────────────────────────
@router.get("/{profession_id}/fields", response_model=list[ProfessionFieldOut])
def list_fields(
    profession_id: int, service: ProfessionServiceDep, _staff: StaffDep,
) -> list[ProfessionFieldOut]:
    return [ProfessionFieldOut.model_validate(f) for f in service.list_fields(profession_id)]


@router.post("/{profession_id}/fields", response_model=ProfessionFieldOut,
             status_code=status.HTTP_201_CREATED)
def create_field(
    profession_id: int,
    payload: ProfessionFieldCreate,
    service: ProfessionServiceDep,
    _staff: Staff = Depends(require_owner),
) -> ProfessionFieldOut:
    return ProfessionFieldOut.model_validate(
        service.create_field(profession_id, payload.model_dump())
    )


@router.patch("/{profession_id}/fields/{field_id}", response_model=ProfessionFieldOut)
def update_field(
    profession_id: int,
    field_id: int,
    payload: ProfessionFieldUpdate,
    service: ProfessionServiceDep,
    _staff: Staff = Depends(require_owner),
) -> ProfessionFieldOut:
    return ProfessionFieldOut.model_validate(
        service.update_field(profession_id, field_id, payload.model_dump(exclude_unset=True))
    )


@router.delete("/{profession_id}/fields/{field_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_field(
    profession_id: int,
    field_id: int,
    service: ProfessionServiceDep,
    _staff: Staff = Depends(require_owner),
) -> None:
    service.delete_field(profession_id, field_id)
