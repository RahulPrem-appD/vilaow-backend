"""Buyer callback requests. POST is the public "call me back" form on the
site — no auth, anyone can hit it. Everything else is staff worklist."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.clients import client_ip
from app.api.deps import SettingsDep
from app.api.throttle import lead_submissions
from app.db import get_db
from app.models import Event, Lead, LeadStatus, Professional, Staff
from app.schemas import LeadCreate, LeadOut, LeadUpdate
from app.security import current_staff, require_owner

router = APIRouter(prefix="/api/leads", tags=["leads"])


def _stored(payload: LeadCreate) -> dict:
    """The submitted fields, without the honeypot."""
    return payload.model_dump(exclude={"website"})


def _accepted_but_discarded(payload: LeadCreate) -> LeadOut:
    """A response shaped exactly like a successful one, for a submission that
    was not stored. Built by hand rather than from a throwaway ORM object, so
    nothing can accidentally be added to the session."""
    return LeadOut(
        id=0,
        status=LeadStatus.new,
        created_at=datetime.now(timezone.utc),
        callback_due=None,
        contacted_at=None,
        admin_notes=None,
        **_stored(payload),
    )


@router.post("", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(
    payload: LeadCreate,
    request: Request,
    settings: SettingsDep,
    db: Session = Depends(get_db),
) -> LeadOut:
    """The public callback form.

    The introduction form has a rate limit, a honeypot and bounded fields; this
    one had none of the three, so the caller worklist could be filled by anyone
    with a script, and a malformed submission reached a buyer as a 500.
    """
    if payload.website:
        # The honeypot. Answer exactly as a success would, so whatever filled
        # it in learns nothing, and store nothing.
        return _accepted_but_discarded(payload)

    if not lead_submissions.check(client_ip(request, settings)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Thank you — we already have your request and will be in touch.",
        )

    # A professional_id that points at nothing was a foreign key error, which
    # arrived as an unhandled 500. Dropping it keeps the enquiry, which is the
    # part that matters to the buyer.
    data = _stored(payload)
    if data.get("professional_id") is not None:
        if db.get(Professional, data["professional_id"]) is None:
            data["professional_id"] = None

    lead = Lead(**data)
    lead.set_callback_due()  # falls back to "now" — created_at isn't known until insert
    db.add(lead)
    db.flush()  # need lead.id for the Event below

    db.add(Event(
        lead_id=lead.id,
        actor_id=None,
        actor_label="buyer (public form)",
        kind="lead_created",
        detail=lead.message,
    ))

    db.commit()
    db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.get("", response_model=list[LeadOut])
def list_leads(
    status_filter: LeadStatus | None = Query(None, alias="status"),
    overdue: bool | None = None,
    db: Session = Depends(get_db),
    _staff: Staff = Depends(current_staff),
) -> list[LeadOut]:
    now = datetime.now(timezone.utc)
    is_overdue = (Lead.status == LeadStatus.new) & (Lead.callback_due < now)

    stmt = select(Lead)
    if status_filter is not None:
        stmt = stmt.where(Lead.status == status_filter)
    if overdue is True:
        stmt = stmt.where(is_overdue)
    elif overdue is False:
        stmt = stmt.where(~is_overdue)

    # Default ordering: overdue first, then oldest callback_due.
    stmt = stmt.order_by(is_overdue.desc(), Lead.callback_due.asc())
    leads = db.scalars(stmt).all()
    return [LeadOut.model_validate(lead) for lead in leads]


@router.patch("/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: int,
    payload: LeadUpdate,
    db: Session = Depends(get_db),
    staff: Staff = Depends(current_staff),
) -> LeadOut:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")

    data = payload.model_dump(exclude_unset=True)
    became_contacted = data.get("status") == LeadStatus.contacted and lead.status != LeadStatus.contacted
    if became_contacted:
        lead.contacted_at = datetime.now(timezone.utc)

    for field, value in data.items():
        setattr(lead, field, value)

    detail_parts = []
    if "status" in data:
        detail_parts.append(f"status -> {data['status'].value}")
    if "admin_notes" in data and data["admin_notes"]:
        detail_parts.append(data["admin_notes"])

    db.add(Event(
        lead_id=lead.id,
        actor_id=staff.id,
        actor_label=staff.name,
        kind="lead_updated",
        detail="; ".join(detail_parts) or None,
    ))

    db.commit()
    db.refresh(lead)
    return LeadOut.model_validate(lead)


@router.delete("/{lead_id}", status_code=status.HTTP_204_NO_CONTENT)
def erase_lead(
    lead_id: int,
    db: Session = Depends(get_db),
    staff: Staff = Depends(require_owner),
) -> None:
    """Erasure of a buyer's callback request.

    There was no way to delete one. The privacy page says "You can ask us to
    delete your data at any time and we will do it", and introductions had a
    button while leads — the other public form, holding a name, a phone number
    and often an email — had nothing. The promise was undeliverable for half
    the personal data on the site.

    Owner-only, like the introduction equivalent: deleting a record someone is
    working is not a routine caller action.
    """
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Lead not found")

    email, phone = lead.buyer_email, lead.buyer_phone

    # The lead's own events go with it — they are about this enquiry and
    # nothing else, so there is no history worth keeping once it is erased.
    db.query(Event).filter(Event.lead_id == lead_id).delete(synchronize_session=False)
    db.delete(lead)

    # And anything the enquiry left in event text elsewhere. Deleting the row
    # while the address sits in an audit trail is not erasure.
    for column in (email, phone):
        if not column:
            continue
        for event in db.scalars(select(Event).where(Event.detail.contains(column))).all():
            event.detail = (event.detail or "").replace(column, "[erased]")

    db.add(Event(
        actor_id=staff.id,
        actor_label=staff.name,
        kind="lead_erased",
        detail=f"lead #{lead_id} deleted at request",
    ))
    db.commit()
