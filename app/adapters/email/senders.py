"""Transports. Each one only knows how to deliver a Message.

Three implementations of the same port, chosen at composition time:

  * `SmtpEmailSender` — the real one.
  * `NullEmailSender`  — no credentials configured. Says so, loudly and in the
    response, rather than quietly doing nothing: a pipeline that stalls with
    no explanation is worse than one that fails.
  * `InMemoryEmailSender` — the test double. It is a first-class implementation
    rather than a monkeypatched module global, which is the difference between
    a seam and a hack.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from app.ports.email import EmailSender, Message, SendResult

log = logging.getLogger("vilaow.mail")


class SmtpEmailSender(EmailSender):
    def __init__(self, *, host: str, port: int, user: str, password: str,
                 from_address: str, from_name: str) -> None:
        self._host = host
        self._port = port
        self._user = user
        self._password = password
        self._from_address = from_address
        self._from_name = from_name

    def _build(self, message: Message) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = formataddr((self._from_name, self._from_address))
        msg["To"] = message.to
        msg["Subject"] = message.subject
        msg.set_content(message.text)
        if message.html:
            msg.add_alternative(message.html, subtype="html")
        return msg

    def send(self, message: Message) -> SendResult:
        try:
            context = ssl.create_default_context()
            with smtplib.SMTP(self._host, self._port, timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(self._user, self._password)
                server.send_message(self._build(message))
            log.info("sent %r to %s", message.subject, message.to)
            return SendResult(True, "sent")
        except smtplib.SMTPAuthenticationError:
            log.exception("SMTP rejected the credentials")
            return SendResult(False, "the mail server rejected the credentials")
        except Exception as exc:  # noqa: BLE001 — report, never crash the request
            log.exception("sending failed")
            return SendResult(False, f"{type(exc).__name__}: {exc}")


class NullEmailSender(EmailSender):
    """No SMTP configured. Reports the failure instead of hiding it."""

    def send(self, message: Message) -> SendResult:
        log.warning("email not configured; would have sent %r to %s", message.subject, message.to)
        return SendResult(False, "email is not configured on this deployment")


class InMemoryEmailSender(EmailSender):
    """Keeps what it was asked to send, so tests can read it back.

    Used by the suite in place of the real transport. Because it satisfies the
    same port, nothing under test has to know it exists — which is exactly what
    the old module-level `send` could not offer.
    """

    def __init__(self) -> None:
        self.outbox: list[Message] = []

    def send(self, message: Message) -> SendResult:
        self.outbox.append(message)
        return SendResult(True, "captured in memory")

    # Small conveniences, because every test that reads an outbox wants these.
    def to(self, address: str) -> list[Message]:
        return [m for m in self.outbox if m.to == address]

    def last(self) -> Message | None:
        return self.outbox[-1] if self.outbox else None

    def clear(self) -> None:
        self.outbox.clear()
