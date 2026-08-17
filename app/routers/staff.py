"""Staff accounts. Owner only, end to end — a caller cannot create or edit
staff, including their own account. password_hash never appears in StaffOut,
so there is no response path that can leak it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Staff
from app.schemas import StaffCreate, StaffOut, StaffUpdate
from app.security import hash_password, require_owner

router = APIRouter(prefix="/api/staff", tags=["staff"])


@router.get("", response_model=list[StaffOut])
def list_staff(db: Session = Depends(get_db), _staff: Staff = Depends(require_owner)) -> list[StaffOut]:
    rows = db.scalars(select(Staff).order_by(Staff.name)).all()
    return [StaffOut.model_validate(s) for s in rows]


@router.post("", response_model=StaffOut, status_code=status.HTTP_201_CREATED)
def create_staff(
    payload: StaffCreate,
    db: Session = Depends(get_db),
    _staff: Staff = Depends(require_owner),
) -> StaffOut:
    email = payload.email.strip().lower()
    if db.scalar(select(Staff).where(Staff.email == email)) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "A staff member with this email already exists")

    staff = Staff(
        name=payload.name,
        email=email,
        password_hash=hash_password(payload.password),
        role=payload.role,
        active=payload.active,
    )
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return StaffOut.model_validate(staff)


@router.patch("/{staff_id}", response_model=StaffOut)
def update_staff(
    staff_id: int,
    payload: StaffUpdate,
    db: Session = Depends(get_db),
    _staff: Staff = Depends(require_owner),
) -> StaffOut:
    staff = db.get(Staff, staff_id)
    if staff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Staff not found")

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(staff, field, value)

    db.commit()
    db.refresh(staff)
    return StaffOut.model_validate(staff)
