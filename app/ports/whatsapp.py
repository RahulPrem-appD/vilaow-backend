"""Sending WhatsApp, as an interface — the same shape as email (app/ports/email.py).

WhatsApp lets a business start a conversation only with a template Meta has
approved in advance, so a message here is never free text: it is one of our
templates, a language, and the values for its blanks. The wording lives in
app/adapters/whatsapp/templates.py; how it travels is an adapter — Twilio for
real, or the pretend sender that writes it down and sends nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class WhatsAppMessage:
    to: str                         # E.164, "+447700900123"
    template: str                   # our key: "thank_buyer", "remind_pro", …
    language: str                   # "en", "el" or "fr"
    variables: tuple[str, ...]      # the values for {{1}}, {{2}}, … in order


@dataclass(frozen=True)
class WhatsAppResult:
    ok: bool
    detail: str
    # The provider's id for the message. A reply and a delivery report both
    # carry it, which is how they find the step they belong to.
    provider_id: str | None = None
    # A hiccup worth another try — the network, a rate limit, the provider
    # having a bad minute — as opposed to a refusal that will not change.
    retry: bool = False


class WhatsAppSender(Protocol):
    """Anything that can try to deliver a WhatsAppMessage.

    Returns a result rather than raising, like EmailSender: a message failing
    must never undo the introduction that caused it.
    """

    # "twilio" or "pretend", recorded on every step it sends.
    transport: str

    def send(self, message: WhatsAppMessage) -> WhatsAppResult: ...
