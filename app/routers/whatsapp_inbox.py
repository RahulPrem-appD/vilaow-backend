"""HTTP for the staff WhatsApp inbox (app/services/whatsapp_inbox.py).

Staff only, and erasing a conversation is the owner's. Messages arrive through
Meta's webhook in app/routers/follow_ups.py, which this file does not touch.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from app.adapters.whatsapp.templates import TEMPLATES
from app.api.deps import InboxServiceDep, MetaWebhookDep, StaffDep, WhatsAppAssistantDep
from app.models import Staff
from app.schemas import (
    InboxMessageOut,
    InboxOut,
    InboxTemplateOut,
    InboxThreadOut,
    SendTemplateIn,
    SendTextIn,
)
from app.security import require_owner

router = APIRouter(prefix="/api/whatsapp", tags=["whatsapp"])


@router.get("/conversations", response_model=InboxOut)
def list_conversations(inbox: InboxServiceDep, hook: MetaWebhookDep,
                       assistant: WhatsAppAssistantDep, _staff: StaffDep) -> InboxOut:
    return InboxOut(
        receiving=hook is not None,
        sending=inbox.sending,
        ai_replies=assistant is not None and inbox.sending is not None,
        items=inbox.conversations(),
    )


@router.get("/conversations/{number}", response_model=InboxThreadOut)
def read_conversation(number: str, inbox: InboxServiceDep, _staff: StaffDep) -> InboxThreadOut:
    """Every message with one person, oldest first. Empty for a number nobody
    has written to yet, so a first template can go from the same page."""
    return InboxThreadOut.model_validate(inbox.thread(number))


@router.post("/conversations/{number}/read", status_code=status.HTTP_204_NO_CONTENT)
def mark_read(number: str, inbox: InboxServiceDep, _staff: StaffDep) -> Response:
    inbox.mark_read(number)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/conversations/{number}", status_code=status.HTTP_204_NO_CONTENT)
def erase_conversation(number: str, inbox: InboxServiceDep,
                       _owner: Staff = Depends(require_owner)) -> Response:
    inbox.erase(number)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/unread")
def unread(inbox: InboxServiceDep, _staff: StaffDep) -> dict[str, int]:
    """For the count in the admin's menu: people with a message nobody has read."""
    return {"conversations": inbox.unread()}


@router.get("/templates", response_model=list[InboxTemplateOut])
def list_templates(_staff: StaffDep) -> list[InboxTemplateOut]:
    return [
        InboxTemplateOut(
            key=t.key, name=t.meta_name, recipient=t.recipient, purpose=t.purpose,
            texts=dict(t.texts), blanks=[what for what, _ in t.samples],
            examples=[sample for _, sample in t.samples],
            buttons=[labels["en"] for _, labels in t.buttons],
        )
        for t in TEMPLATES.values()
    ]


@router.post("/text", response_model=InboxMessageOut, status_code=status.HTTP_201_CREATED)
def send_text(body: SendTextIn, inbox: InboxServiceDep, staff: StaffDep) -> InboxMessageOut:
    """Staff's own words: only within 24 hours of the person's last message."""
    return InboxMessageOut.model_validate(inbox.send_text(body.number, body.text, staff=staff))


@router.post("/template", response_model=InboxMessageOut, status_code=status.HTTP_201_CREATED)
def send_template(body: SendTemplateIn, inbox: InboxServiceDep, staff: StaffDep) -> InboxMessageOut:
    """An approved template, to anyone, at any time."""
    return InboxMessageOut.model_validate(inbox.send_template(
        body.number, body.template, body.language, body.values, staff=staff))
