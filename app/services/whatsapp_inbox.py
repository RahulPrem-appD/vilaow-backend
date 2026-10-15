"""The WhatsApp inbox: every message to and from Vilaow's number, for staff.

What staff can do with it, and Meta's rule for each:

  * read every message people send. Each is saved as it arrives, through the
    webhook (app/routers/follow_ups.py);
  * answer in their own words, but only within 24 hours of the person's last
    message. After that Meta refuses free text;
  * send an approved template to anyone, at any time. It is the one way to
    start a conversation, or to restart one;
  * hear about a new message: an email to every active staff member, at most
    one per conversation every ten minutes, and an unread count in the
    admin's menu;
  * let the website's assistant answer, when it is switched on
    (WHATSAPP_AI_REPLIES): only typed messages, only within the 24 hours,
    never to a professional, never once staff have answered by hand, and at
    most twenty answers a day to one person.

The follow-up's own messages (app/services/follow_ups.py) are written here
too, so a conversation reads in order whoever sent what.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.email import templates as emails
from app.adapters.whatsapp import templates
from app.domain.errors import Conflict, Invalid
from app.domain.phones import buyer_whatsapp, professional_whatsapp
from app.models import InboxMessage, Introduction, Professional, Staff
from app.ports.clock import Clock
from app.ports.email import EmailSender
from app.ports.whatsapp import WhatsAppChatSender, WhatsAppMessage, WhatsAppResult

if TYPE_CHECKING:
    from app.services.assistant import Assistant

log = logging.getLogger("vilaow.whatsapp")

# Meta's customer service window: free text only this long after the person's
# last message.
WINDOW = timedelta(hours=24)
# What does not open it: an emoji on a message, WhatsApp's own notices.
NO_WINDOW = ("reaction", "system")
# One email per conversation in this time, however many messages arrive.
ALERT_QUIET = timedelta(minutes=10)
# The assistant's answers to one person in a day: anyone can write to the
# number, and every answer is paid for.
AI_PER_DAY = 20
# How much of a conversation the assistant reads, at most.
AI_HISTORY = 30
# WhatsApp's own limit for a text message.
TEXT_LIMIT = 4096
# One template blank, as staff fill it in.
VALUE_LIMIT = 300
# Delivery reports only ever move a message forward.
_RANK = {"sent": 1, "delivered": 2, "read": 3}

_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_BLANKS = re.compile(r"\{\{(\d+)\}\}")
_LINK = re.compile(r"\[([^\]\n]+)\]\(([^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__", re.S)
_HEADING = re.compile(r"^#{1,6}[ \t]+(.+)$", re.M)

# What a message that is not words looks like in the inbox and in an email.
# The file itself is not fetched from Meta: the inbox shows that it came.
_KIND_LABELS = {
    "image": "[Photo]", "video": "[Video]", "audio": "[Voice message]",
    "document": "[Document]", "sticker": "[Sticker]", "location": "[Location]",
    "contacts": "[Contact card]", "reaction": "[Reacted]",
}


def describe(kind: str, body: str | None) -> str:
    """A message as text, whatever it was."""
    if kind in ("text", "button", "interactive", "template"):
        return body or ""
    label = _KIND_LABELS.get(kind, "[A kind of message the inbox cannot show]")
    return f"{label} {body}" if body else label


def whatsapp_text(markdown: str, site_url: str) -> str:
    """The assistant's Markdown, as WhatsApp shows text.

    It writes for the website's chat: links like [the Cost Guide](/costs),
    and **bold**. WhatsApp shows neither: a link has to be the address
    itself, and bold is one star each side.
    """
    base = site_url.rstrip("/")

    def link(match: re.Match[str]) -> str:
        label, href = match.group(1).strip(), match.group(2)
        if href.startswith("/"):
            href = base + href
        elif not href.startswith(("https://", "http://")):
            return label
        return href if label in (href, match.group(2)) else f"{label} ({href})"

    text = _LINK.sub(link, markdown)
    text = _BOLD.sub(lambda m: f"*{m.group(1) or m.group(2)}*", text)
    text = _HEADING.sub(lambda m: f"*{m.group(1).strip()}*", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) > TEXT_LIMIT:
        text = text[: TEXT_LIMIT - 1].rstrip() + "…"
    return text


def one_line(value: str | None, limit: int) -> str | None:
    text = " ".join((value or "").split())[:limit]
    return text or None


def _number(raw: str) -> str:
    number = buyer_whatsapp(raw).number
    if number is None or not _E164.match(number):
        raise Invalid("Enter the full number with its country code, like +44 7700 900123.")
    return number


class InboxService:
    def __init__(self, db: Session, *, clock: Clock, sender: WhatsAppChatSender | None) -> None:
        self._db = db
        self._clock = clock
        self._sender = sender

    @property
    def sending(self) -> str | None:
        """"meta", "pretend", or None when nothing can be sent."""
        return self._sender.transport if self._sender is not None else None

    # ── receiving ───────────────────────────────────────────────────────────
    def receive(self, *, number: str, kind: str, text: str | None, provider_id: str | None,
                profile_name: str | None, at: datetime | None) -> InboxMessage | None:
        """Keeps one message somebody sent. None when it was kept already, as
        when Meta delivers a callback again, or when it is not from a number."""
        if not _E164.match(number):
            log.warning("whatsapp: ignored a message from %r, which is not a number", number)
            return None
        if provider_id and self._db.scalar(
                select(InboxMessage.id).where(InboxMessage.provider_id == provider_id)):
            return None
        now = self._clock.now()
        message = InboxMessage(
            number=number, direction="in", kind=kind[:16], body=text or None,
            status="received", provider_id=provider_id,
            profile_name=one_line(profile_name, 120),
            # Never later than now: a phone with its clock wrong must not hold
            # the 24 hours open.
            at=min(at, now) if at else now,
        )
        self._db.add(message)
        try:
            self._db.commit()
        except IntegrityError:
            # The same message, delivered twice at the same moment.
            self._db.rollback()
            return None
        return message

    def link(self, message_id: int, introduction_id: int, *, handled: bool) -> None:
        """The message answered the follow-up of this introduction. `handled`:
        it was a Yes or No the follow-up has acted on, so nobody needs to read it."""
        values: dict = {"introduction_id": introduction_id}
        if handled:
            values["read_at"] = self._clock.now()
        self._db.execute(update(InboxMessage).where(InboxMessage.id == message_id).values(**values))
        self._db.commit()

    def record_delivery(self, *, provider_id: str, status: str, error_code: str | None,
                        error_title: str | None = None) -> None:
        message = self._db.scalar(select(InboxMessage).where(
            InboxMessage.provider_id == provider_id, InboxMessage.direction == "out"))
        if message is None or message.status == "failed":
            return
        if status in ("failed", "undelivered"):
            message.status = "failed"
            reason = f"{error_title} " if error_title else ""
            message.detail = (f"WhatsApp could not deliver it: {reason}"
                              f"(error {error_code or 'unknown'})")
        elif _RANK.get(status, 0) > _RANK.get(message.status, 0):
            message.status = status
        else:
            return
        self._db.commit()

    # ── reading ─────────────────────────────────────────────────────────────
    def open_until(self, number: str) -> datetime | None:
        """Until when free text may go to this number; None when it may not."""
        last = self._db.scalar(select(func.max(InboxMessage.at)).where(
            InboxMessage.number == number, InboxMessage.direction == "in",
            InboxMessage.kind.not_in(NO_WINDOW)))
        if last is None or last + WINDOW <= self._clock.now():
            return None
        return last + WINDOW

    def conversations(self) -> list[dict]:
        """One line per person, the latest first."""
        latest = self._db.scalars(
            select(InboxMessage).distinct(InboxMessage.number)
            .order_by(InboxMessage.number, InboxMessage.at.desc(), InboxMessage.id.desc())
        ).all()
        if not latest:
            return []
        unread = dict(self._db.execute(
            select(InboxMessage.number, func.count()).where(
                InboxMessage.direction == "in", InboxMessage.read_at.is_(None))
            .group_by(InboxMessage.number)).all())
        last_in = dict(self._db.execute(
            select(InboxMessage.number, func.max(InboxMessage.at)).where(
                InboxMessage.direction == "in", InboxMessage.kind.not_in(NO_WINDOW))
            .group_by(InboxMessage.number)).all())
        people = self._people([m.number for m in latest])
        now = self._clock.now()
        rows = []
        for message in sorted(latest, key=lambda m: (m.at, m.id), reverse=True):
            until = last_in.get(message.number)
            rows.append({
                "number": message.number,
                **people[message.number],
                "last": self._out(message, {}),
                "unread": unread.get(message.number, 0),
                "open_until": until + WINDOW if until and until + WINDOW > now else None,
            })
        return rows

    def thread(self, number: str) -> dict:
        number = _number(number)
        messages = self._db.scalars(
            select(InboxMessage).where(InboxMessage.number == number)
            .order_by(InboxMessage.at, InboxMessage.id)).all()
        staff_ids = {m.staff_id for m in messages if m.staff_id}
        names = dict(self._db.execute(
            select(Staff.id, Staff.name).where(Staff.id.in_(staff_ids))).all()) if staff_ids else {}
        return {
            "number": number,
            **self._people([number])[number],
            "open_until": self.open_until(number),
            "sending": self.sending,
            "unread": sum(1 for m in messages if m.direction == "in" and m.read_at is None),
            "messages": [self._out(m, names) for m in messages],
        }

    def unread(self) -> int:
        """How many people are waiting to be read."""
        return self._db.scalar(
            select(func.count(func.distinct(InboxMessage.number))).where(
                InboxMessage.direction == "in", InboxMessage.read_at.is_(None))) or 0

    def mark_read(self, number: str) -> None:
        self._db.execute(
            update(InboxMessage).where(
                InboxMessage.number == _number(number), InboxMessage.direction == "in",
                InboxMessage.read_at.is_(None))
            .values(read_at=self._clock.now()))
        self._db.commit()

    def erase(self, number: str) -> int:
        """Every message to and from one number, gone: erasure on request."""
        number = _number(number)
        count = self._db.execute(
            delete(InboxMessage).where(InboxMessage.number == number)).rowcount
        self._db.commit()
        log.info("whatsapp: erased %d messages of one conversation", count)
        return count

    def _people(self, numbers: list[str]) -> dict[str, dict]:
        """Who each number is, as far as Vilaow knows: a professional on its
        books, the buyer of an introduction, or the name they use on WhatsApp."""
        wanted = set(numbers)
        found: dict[str, dict] = {
            n: {"name": None, "role": None, "professional_id": None, "introduction_id": None}
            for n in wanted
        }
        for pro_id, contact, business, phone in self._db.execute(select(
                Professional.id, Professional.contact_name, Professional.business_name,
                Professional.phone).where(Professional.phone.is_not(None))):
            number = professional_whatsapp(phone).number
            if number in wanted and found[number]["role"] is None:
                found[number].update(name=contact or business, role="professional",
                                     professional_id=pro_id)
        others = [n for n in wanted if found[n]["role"] is None]
        if others:
            for number, intro_id, buyer in self._db.execute(
                    select(InboxMessage.number, Introduction.id, Introduction.buyer_name)
                    .join(Introduction, Introduction.id == InboxMessage.introduction_id)
                    .where(InboxMessage.number.in_(others))
                    .order_by(InboxMessage.at.desc())):
                if found[number]["role"] is None:
                    found[number].update(name=buyer, role="buyer", introduction_id=intro_id)
            for number, name in self._db.execute(
                    select(InboxMessage.number, InboxMessage.profile_name)
                    .where(InboxMessage.number.in_(others), InboxMessage.profile_name.is_not(None))
                    .order_by(InboxMessage.at.desc())):
                if found[number]["name"] is None:
                    found[number]["name"] = name
        return found

    @staticmethod
    def _out(message: InboxMessage, staff_names: dict[int, str]) -> dict:
        return {
            "id": message.id,
            "number": message.number,
            "direction": message.direction,
            "kind": message.kind,
            "text": describe(message.kind, message.body),
            "template": message.template,
            "status": message.status,
            "detail": message.detail,
            "sent_by": message.sent_by,
            "staff_name": staff_names.get(message.staff_id) if message.staff_id else None,
            "at": message.at,
        }

    # ── sending ─────────────────────────────────────────────────────────────
    def send_text(self, number: str, text: str, *, staff: Staff | None) -> dict:
        """Staff answering in their own words."""
        number = _number(number)
        text = (text or "").strip()
        if not text:
            raise Invalid("Write a message first.")
        if len(text) > TEXT_LIMIT:
            raise Invalid(f"WhatsApp takes at most {TEXT_LIMIT} characters in one message.")
        sender = self._ready()
        if self.open_until(number) is None:
            raise Conflict("It is more than 24 hours since they last wrote, so WhatsApp only "
                           "allows a template. Send one, and you can write freely once they answer.")
        message = self._kept(number, "text", text, sender.send_text(number, text),
                             sent_by="staff", staff=staff)
        return self._out(message, {staff.id: staff.name} if staff else {})

    def send_template(self, number: str, key: str, language: str, values: list[str], *,
                      staff: Staff | None) -> dict:
        """One of the approved templates, to anyone, at any time."""
        number = _number(number)
        template = templates.TEMPLATES.get(key)
        if template is None:
            raise Invalid("There is no such template.")
        if language not in template.texts:
            raise Invalid("That template is not in that language.")
        blanks = len(set(_BLANKS.findall(template.texts[language])))
        cleaned = [one_line(value, VALUE_LIMIT + 1) for value in values]
        if len(cleaned) != blanks or not all(cleaned):
            raise Invalid(f"Fill in all {blanks} blanks.")
        if any(len(value) > VALUE_LIMIT for value in cleaned):
            raise Invalid(f"Each blank takes at most {VALUE_LIMIT} characters.")
        variables = tuple(cleaned)
        sender = self._ready()
        result = sender.send(WhatsAppMessage(to=number, template=key, language=language,
                                             variables=variables))
        message = self._kept(number, "template", templates.render(key, language, variables),
                             result, sent_by="staff", staff=staff, template=key)
        return self._out(message, {staff.id: staff.name} if staff else {})

    def _ready(self) -> WhatsAppChatSender:
        if self._sender is None:
            raise Conflict("Sending WhatsApp messages is not set up on this server yet.")
        return self._sender

    def _kept(self, number: str, kind: str, body: str, result: WhatsAppResult, *,
              sent_by: str, staff: Staff | None = None, template: str | None = None) -> InboxMessage:
        """A message we sent, or tried to: a refusal is kept too, with Meta's reason."""
        assert self._sender is not None
        if not result.ok:
            status = "failed"
        elif self._sender.transport == "pretend":
            status = "pretend"
        else:
            status = "sent"
        message = InboxMessage(
            number=number, direction="out", kind=kind, body=body, template=template,
            status=status, detail=None if result.ok else result.detail,
            provider_id=result.provider_id if result.ok else None, sent_by=sent_by,
            staff_id=staff.id if staff else None, at=self._clock.now(),
        )
        self._db.add(message)
        self._db.commit()
        return message

    # ── after a message arrives ─────────────────────────────────────────────
    def alert(self, message_id: int, *, email: EmailSender,
              inbox_link: Callable[[str], str]) -> int:
        """Email staff about a new message. How many emails went."""
        message = self._db.get(InboxMessage, message_id)
        if (message is None or message.direction != "in" or message.alerted_at is not None
                or message.read_at is not None or message.kind in NO_WINDOW):
            return 0
        now = self._clock.now()
        if self._db.scalar(select(InboxMessage.id).where(
                InboxMessage.number == message.number, InboxMessage.direction == "in",
                InboxMessage.alerted_at >= now - ALERT_QUIET).limit(1)):
            return 0
        # Marked before sending: a second message landing meanwhile stays quiet.
        message.alerted_at = now
        self._db.commit()
        person = self._people([message.number])[message.number]
        who = one_line(person["name"], 60) or message.number
        sent = 0
        for address in self._db.scalars(select(Staff.email).where(Staff.active.is_(True))):
            result = email.send(emails.whatsapp_alert(
                to=address, who=who, number=message.number,
                text=describe(message.kind, message.body), link=inbox_link(message.number)))
            sent += result.ok
            if not result.ok:
                log.warning("whatsapp: the alert did not go: %s", result.detail)
        return sent

    def ai_reply(self, message_id: int, *, assistant: Assistant, site_url: str) -> dict | None:
        """The assistant answers a typed message, when it may. None when not."""
        message = self._db.get(InboxMessage, message_id)
        if (message is None or message.direction != "in" or message.kind != "text"
                or not (message.body or "").strip() or self._sender is None):
            return None
        number, now = message.number, self._clock.now()
        if message.at + WINDOW <= now or self._newer(message):
            return None
        if self._people([number])[number]["role"] == "professional":
            return None
        if self._taken_over(number, now):
            return None
        answered = self._db.scalar(select(func.count()).select_from(InboxMessage).where(
            InboxMessage.number == number, InboxMessage.sent_by == "assistant",
            InboxMessage.at >= now - timedelta(days=1))) or 0
        if answered >= AI_PER_DAY:
            log.warning("whatsapp: the assistant has answered one number %d times today", answered)
            return None
        history = self._history(number, since=now - WINDOW)
        if not history:
            return None

        words: list[str] = []
        for event in assistant.reply(history):
            if event["type"] == "text":
                words.append(event["text"])
            elif event["type"] == "error":
                log.warning("whatsapp: the assistant could not answer (%s)", event.get("code"))
                return None
        text = whatsapp_text("".join(words), site_url)
        # Answering takes seconds. If they wrote again meanwhile, the newer
        # message gets the answer, with this one in it. If staff answered
        # meanwhile, theirs stands alone.
        if not text or self._newer(message) or self._taken_over(number, now):
            return None
        reply = self._kept(number, "text", text, self._sender.send_text(number, text),
                           sent_by="assistant")
        return self._out(reply, {})

    def _taken_over(self, number: str, now: datetime) -> bool:
        """Staff wrote to them in the last 24 hours: a person has taken over."""
        return self._db.scalar(select(InboxMessage.id).where(
            InboxMessage.number == number, InboxMessage.sent_by == "staff",
            InboxMessage.at >= now - WINDOW).limit(1)) is not None

    def _newer(self, message: InboxMessage) -> bool:
        return self._db.scalar(select(InboxMessage.id).where(
            InboxMessage.number == message.number, InboxMessage.direction == "in",
            InboxMessage.id > message.id, InboxMessage.kind.not_in(NO_WINDOW)).limit(1)) is not None

    def _history(self, number: str, *, since: datetime) -> list[dict[str, str]]:
        """The last day of the conversation, as the assistant reads one: what
        they wrote, and what Vilaow answered, turn by turn, ending with theirs."""
        rows = list(reversed(self._db.scalars(
            select(InboxMessage).where(InboxMessage.number == number, InboxMessage.at >= since)
            .order_by(InboxMessage.at.desc(), InboxMessage.id.desc()).limit(AI_HISTORY)).all()))
        turns: list[dict[str, str]] = []
        for row in rows:
            if not row.body:
                continue
            if row.direction == "in":
                if row.kind not in ("text", "button", "interactive"):
                    continue
                role = "user"
            else:
                if row.status == "failed":
                    continue   # they never saw it
                role = "assistant"
            if turns and turns[-1]["role"] == role:
                turns[-1]["content"] += "\n\n" + row.body
            else:
                turns.append({"role": role, "content": row.body})
        # A conversation the model reads opens with the person.
        while turns and turns[0]["role"] == "assistant":
            turns.pop(0)
        return turns if turns and turns[-1]["role"] == "user" else []


def after_message(session_factory: Callable[[], Session], message_id: int, *, clock: Clock,
                  sender: WhatsAppChatSender | None, email: EmailSender,
                  inbox_link: Callable[[str], str], assistant: Assistant | None,
                  site_url: str) -> None:
    """What follows a new message, once Meta has had its answer: the
    assistant's reply, then the email to staff. In that order because the
    person is waiting, and a mail server that hangs can take twenty seconds
    a recipient to give up. A session of its own, because the request's has
    closed by now."""
    with session_factory() as db:
        inbox = InboxService(db, clock=clock, sender=sender)
        if assistant is not None:
            try:
                inbox.ai_reply(message_id, assistant=assistant, site_url=site_url)
            except Exception:   # noqa: BLE001 — the email below still deserves its chance
                log.exception("whatsapp: the assistant's reply failed")
                db.rollback()
        try:
            inbox.alert(message_id, email=email, inbox_link=inbox_link)
        except Exception:   # noqa: BLE001 — logged; the message is kept either way
            log.exception("whatsapp: alerting staff failed")
