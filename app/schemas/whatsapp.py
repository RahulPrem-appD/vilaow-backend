"""The staff WhatsApp inbox (app/services/whatsapp_inbox.py)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

__all__ = [
    "InboxMessageOut", "InboxConversationOut", "InboxOut", "InboxThreadOut",
    "InboxTemplateOut", "SendTextIn", "SendTemplateIn",
]


class InboxMessageOut(BaseModel):
    id: int
    number: str
    direction: Literal["in", "out"]
    kind: str
    # What to show: the words, or what kind of message it was.
    text: str
    template: str | None
    # In: "received". Out: "sent", "delivered", "read", "failed" or "pretend".
    status: str
    detail: str | None
    sent_by: Literal["staff", "assistant", "follow-up"] | None
    staff_name: str | None
    at: datetime


class _Person(BaseModel):
    number: str
    name: str | None
    role: Literal["professional", "buyer"] | None
    professional_id: int | None
    introduction_id: int | None
    # Until when staff may write freely; None when only a template may go.
    open_until: datetime | None


class InboxConversationOut(_Person):
    last: InboxMessageOut
    unread: int


class InboxOut(BaseModel):
    # Whether messages reach the inbox at all: Meta's webhook is set up.
    receiving: bool
    # "meta", "pretend", or None when nothing can be sent.
    sending: str | None
    # Whether the assistant answers typed messages.
    ai_replies: bool
    items: list[InboxConversationOut]


class InboxThreadOut(_Person):
    sending: str | None
    # Their messages nobody has opened: the page marks them read.
    unread: int
    messages: list[InboxMessageOut]


class InboxTemplateOut(BaseModel):
    key: str
    name: str
    recipient: str
    purpose: str
    texts: dict[str, str]
    # What each blank holds, in order: "the buyer's first name", …
    blanks: list[str]
    examples: list[str]
    buttons: list[str]


class SendTextIn(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    text: str = Field(min_length=1, max_length=4096)


class SendTemplateIn(BaseModel):
    number: str = Field(min_length=1, max_length=40)
    template: str = Field(min_length=1, max_length=40)
    language: str = Field(min_length=2, max_length=5)
    values: list[str] = Field(max_length=10)
