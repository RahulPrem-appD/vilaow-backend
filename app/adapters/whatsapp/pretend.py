"""WhatsApp senders that send nothing.

`PretendWhatsAppSender` is what WHATSAPP_MODE=pretend runs on: every step is
planned, timed and written down exactly as it would be for real, with the
words it would have said, so the flow can be tried end to end — on the live
site too — before the client's WhatsApp number and templates are approved.

`RecordingWhatsAppSender` is the tests' version: it keeps what it was handed,
and can be told to fail.
"""
from __future__ import annotations

import secrets

from app.adapters.whatsapp.templates import render
from app.ports.whatsapp import WhatsAppMessage, WhatsAppResult


class PretendWhatsAppSender:
    transport = "pretend"

    def send(self, message: WhatsAppMessage) -> WhatsAppResult:
        text = render(message.template, message.language, message.variables)
        return WhatsAppResult(
            ok=True,
            detail=f"Would say: {text}",
            provider_id=f"pretend-{secrets.token_hex(8)}",
        )


class RecordingWhatsAppSender:
    transport = "twilio"

    def __init__(self) -> None:
        self.sent: list[WhatsAppMessage] = []
        # Set to a WhatsAppResult to make the next sends fail with it.
        self.failure: WhatsAppResult | None = None

    def send(self, message: WhatsAppMessage) -> WhatsAppResult:
        if self.failure is not None:
            return self.failure
        self.sent.append(message)
        return WhatsAppResult(ok=True, detail="accepted", provider_id=f"SM{len(self.sent):032d}")
