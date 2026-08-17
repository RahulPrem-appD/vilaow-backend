"""Sending email, as an interface rather than a module full of SMTP.

The old mailer did three jobs in one file: it held the SMTP connection code,
it held the wording of every message, and it was reached as a module-level
function. That last part is why the test suite had to monkeypatch a global to
stop it mailing real people — there was no seam to inject anything into.

Three pieces now, and each can change without touching the others:

  * `Message` — what to send. Data, nothing else.
  * `EmailSender` — how to send it. A protocol, so SMTP is one implementation
    and the tests' capturing sender is another, on equal terms.
  * the templates (app/adapters/email/templates.py) — the wording. Adding an
    email is a new pure function there; it never touches transport.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class Message:
    to: str
    subject: str
    text: str
    html: str | None = None


@dataclass(frozen=True)
class SendResult:
    ok: bool
    detail: str


class EmailSender(Protocol):
    """Anything that can try to deliver a Message.

    Deliberately returns a result rather than raising. Delivery failing must
    not undo the thing that caused it — an agreement is still issued when its
    email bounces, an introduction is still recorded — so callers need to
    carry on and report, not catch.
    """

    def send(self, message: Message) -> SendResult: ...
