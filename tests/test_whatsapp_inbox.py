"""The staff WhatsApp inbox, as tests.

What the client was promised: every message people send is kept and staff
read it in the admin; staff answer in their own words only within 24 hours of
the person's last message; an approved template can go to anyone at any time;
staff hear about new messages, by email and by a count in the menu; and the
website's assistant answers typed questions, inside the same 24 hours.

And what keeps it safe to leave running: nothing is kept that Meta did not
sign, a message Meta delivers twice is kept once, the assistant never answers
a professional or talks over staff, and every refusal says why.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.adapters.email.senders import NullEmailSender
from app.adapters.urls import PublicUrls
from app.adapters.whatsapp.pretend import PretendWhatsAppSender, RecordingWhatsAppSender
from app.api.deps import (
    MetaWebhook,
    get_clock,
    get_email_sender,
    get_follow_up_config,
    get_inbox_sender,
    get_meta_webhook,
    get_urls,
    get_whatsapp_assistant,
    get_whatsapp_sender,
)
from app.db import SessionLocal
from app.models import InboxMessage, Introduction, IntroductionStep, Professional, Stage, Staff
from app.ports.clock import FrozenClock
from app.ports.whatsapp import WhatsAppResult
from app.security import hash_password
from app.services.follow_ups import FollowUpConfig
from app.services.whatsapp_inbox import AI_PER_DAY, InboxService, whatsapp_text

START = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
HOOK = MetaWebhook(app_secret="meta-secret", verify_token="v" * 24)
SITE = "https://vilaow.test"
BUYER = "+447700900123"
PRO = "+306912345678"


# ── set-up ──────────────────────────────────────────────────────────────────
@pytest.fixture
def clock():
    from app.main import app

    frozen = FrozenClock(START)
    app.dependency_overrides[get_clock] = lambda: frozen
    app.dependency_overrides[get_urls] = lambda: PublicUrls(SITE)
    yield frozen
    app.dependency_overrides.pop(get_clock, None)
    app.dependency_overrides.pop(get_urls, None)


class MetaSender(RecordingWhatsAppSender):
    transport = "meta"


@pytest.fixture
def inbox(clock):
    """Meta's webhook open, and a sender that keeps what it is given. The
    follow-up stays off, as it is on the live site today."""
    from app.main import app

    sender = MetaSender()
    app.dependency_overrides[get_meta_webhook] = lambda: HOOK
    app.dependency_overrides[get_inbox_sender] = lambda: sender
    yield sender
    app.dependency_overrides.pop(get_meta_webhook, None)


class FakeAssistant:
    """Answers with fixed words, and keeps every conversation it was shown."""

    def __init__(self, answer: str = "Notary fees are about 1% of the price. "
                                      "See [the Cost Guide](/costs).") -> None:
        self.answer = answer
        self.error: str | None = None
        self.histories: list[list[dict[str, str]]] = []

    def reply(self, history):
        self.histories.append([dict(turn) for turn in history])
        if self.error:
            yield {"type": "error", "code": self.error, "message": "no"}
            return
        yield {"type": "status", "status": "searching"}
        for start in range(0, len(self.answer), 20):
            yield {"type": "text", "text": self.answer[start:start + 20]}
        yield {"type": "done"}


@pytest.fixture
def ai(inbox):
    from app.main import app

    assistant = FakeAssistant()
    app.dependency_overrides[get_whatsapp_assistant] = lambda: assistant
    yield assistant


@pytest.fixture
def follow_up_on_meta(inbox):
    """The follow-up running through Meta too, with a sender of its own."""
    from app.main import app

    sender = MetaSender()
    app.dependency_overrides[get_whatsapp_sender] = lambda: sender
    app.dependency_overrides[get_follow_up_config] = lambda: FollowUpConfig(mode="meta")
    return sender


def _post(client, value: dict, secret: str = HOOK.app_secret):
    body = json.dumps({"object": "whatsapp_business_account",
                       "entry": [{"changes": [{"field": "messages", "value": value}]}]}).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/whatsapp/meta/webhook", content=body,
                       headers={"X-Hub-Signature-256": signature,
                                "Content-Type": "application/json"})


def _message(number: str, wamid: str, *, kind: str = "text", text: str | None = "Hello",
             at: datetime | None = None, name: str | None = None, **extra) -> dict:
    message = {"from": number.lstrip("+"), "id": wamid, "type": kind,
               "timestamp": str(int((at or START).timestamp()))}
    if kind == "text":
        message["text"] = {"body": text}
    message.update(extra)
    value: dict = {"messages": [message]}
    if name:
        value["contacts"] = [{"wa_id": number.lstrip("+"), "profile": {"name": name}}]
    return value


def _write(client, number: str = BUYER, text: str = "Hello", wamid: str = "wamid.in1", **kw):
    response = _post(client, _message(number, wamid, text=text, **kw))
    assert response.status_code == 200, response.text
    return response


def _time(text: str) -> datetime:
    """A time from the API, whatever zone the database answered in."""
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _rows(number: str | None = None) -> list[InboxMessage]:
    with SessionLocal() as db:
        query = select(InboxMessage).order_by(InboxMessage.at, InboxMessage.id)
        if number:
            query = query.where(InboxMessage.number == number)
        return list(db.scalars(query))


def _thread(client, number: str = BUYER) -> dict:
    response = client.get(f"/api/whatsapp/conversations/{number}")
    assert response.status_code == 200, response.text
    return response.json()


def _reply(client, text: str = "Thanks, we will call you today.", number: str = BUYER):
    return client.post("/api/whatsapp/text", json={"number": number, "text": text})


def _template(client, number: str = BUYER, **kw):
    body = {"number": number, "template": "check_buyer", "language": "en",
            "values": ["Sarah", "Kostas Papadopoulos"]}
    body.update(kw)
    return client.post("/api/whatsapp/template", json=body)


def _published(db, professions, **kw) -> Professional:
    base = dict(business_name="Papadopoulos & Partners", contact_name="Kostas Papadopoulos",
                email="kostas@example.com", phone="691 234 5678", city="Heraklion",
                region="Crete", profession_id=professions["lawyer"], slug="kostas",
                published=True, stage=Stage.signed, published_at=START)
    base.update(kw)
    professional = Professional(**base)
    db.add(professional)
    db.commit()
    return professional


def _ask(client) -> int:
    response = client.post("/api/public/introductions", json={
        "slug": "kostas", "buyer_name": "Sarah Mitchell", "buyer_email": "sarah@example.com",
        "buyer_phone": "+44 7700 900123", "message": "Buying a house near Chania.",
        "consent": True,
    })
    assert response.status_code == 201, response.text
    with SessionLocal() as db:
        return db.scalar(select(Introduction.id).order_by(Introduction.id.desc()).limit(1))


# ── keeping what arrives ────────────────────────────────────────────────────
def test_a_message_is_kept_with_who_wrote_it_and_when(client, inbox, as_caller, clock):
    clock.advance(timedelta(minutes=3))
    _write(client, text="Is there a lawyer in Chania?", name="Sarah")

    (row,) = _rows()
    assert (row.number, row.direction, row.kind, row.body) == (
        BUYER, "in", "text", "Is there a lawyer in Chania?")
    # WhatsApp's time, not the moment the callback came: the 24 hours run from it.
    assert row.at == START and row.read_at is None

    listing = client.get("/api/whatsapp/conversations").json()
    assert (listing["receiving"], listing["sending"], listing["ai_replies"]) == (True, "meta", False)
    (person,) = listing["items"]
    assert (person["number"], person["name"], person["unread"]) == (BUYER, "Sarah", 1)
    assert person["last"]["text"] == "Is there a lawyer in Chania?"
    assert _time(person["open_until"]) == START + timedelta(hours=24)

    thread = _thread(client)
    assert [m["text"] for m in thread["messages"]] == ["Is there a lawyer in Chania?"]


def test_a_message_meta_sends_twice_is_kept_once(client, inbox, outbox, owner):
    _write(client)
    _write(client)
    assert len(_rows()) == 1
    assert len(outbox.outbox) == 1   # and staff hear about it once


def test_nothing_is_kept_that_meta_did_not_sign(client, inbox):
    assert _post(client, _message(BUYER, "wamid.forged"), secret="guess").status_code == 403
    assert _rows() == []


def test_every_kind_of_message_is_kept(client, inbox, as_caller):
    for wamid, kind, extra in [
        ("wamid.1", "image", {"image": {"id": "m1", "caption": "The house"}}),
        ("wamid.2", "audio", {"audio": {"id": "m2"}}),
        ("wamid.3", "location", {"location": {"name": "Chania", "latitude": 35.5}}),
        ("wamid.4", "reaction", {"reaction": {"message_id": "wamid.x", "emoji": "👍"}}),
        ("wamid.5", "order", {}),
    ]:
        assert _post(client, _message(BUYER, wamid, kind=kind, **extra)).status_code == 200
    assert [m["text"] for m in _thread(client)["messages"]] == [
        "[Photo] The house", "[Voice message]", "[Location] Chania", "[Reacted] 👍",
        "[A kind of message the inbox cannot show]",
    ]


def test_something_that_is_not_a_number_is_not_kept(client, inbox):
    assert _post(client, _message("not-a-number", "wamid.odd")).status_code == 200
    assert _rows() == []


def test_the_inbox_keeps_messages_before_the_follow_up_runs_on_meta(
        client, db, professions, inbox, clock):
    """Pretend mode wrote a question to this number down, and never sent it. A
    real "yes" from that number is a message, not an answer to it."""
    from app.main import app

    app.dependency_overrides[get_whatsapp_sender] = lambda: PretendWhatsAppSender()
    app.dependency_overrides[get_follow_up_config] = lambda: FollowUpConfig(mode="pretend")
    _published(db, professions)
    _ask(client)

    _post(client, _message(PRO, "wamid.yes", kind="button",
                           button={"payload": "yes", "text": "Yes"}))
    db.expire_all()
    assert [r.body for r in _rows(PRO)] == ["Yes"]
    assert db.scalar(select(Introduction.pro_confirmed_at)) is None
    assert not db.scalars(select(IntroductionStep).where(
        IntroductionStep.kind == "pro_replied")).all()
    # And pretend sends nothing, so it writes nothing into the inbox.
    assert _rows(BUYER) == []


def test_the_follow_ups_messages_show_in_each_conversation(
        client, db, professions, follow_up_on_meta, as_caller):
    _published(db, professions)
    intro_id = _ask(client)

    (thanks,) = _thread(client, BUYER)["messages"]
    assert (thanks["direction"], thanks["kind"], thanks["sent_by"]) == ("out", "template", "follow-up")
    assert thanks["text"].startswith("Hi Sarah, thank you for your request on Vilaow.")
    (details,) = _thread(client, PRO)["messages"]
    assert details["text"].startswith("Γεια σας Kostas, έχετε ένα νέο αίτημα πελάτη")
    assert {r.introduction_id for r in _rows()} == {intro_id}


def test_a_yes_the_follow_up_acts_on_needs_nobody(
        client, db, professions, follow_up_on_meta, ai, outbox, owner, as_caller):
    _published(db, professions)
    intro_id = _ask(client)
    outbox.clear()
    asked = _rows(PRO)[0].provider_id

    _post(client, _message(PRO, "wamid.yes", kind="button", context={"id": asked},
                           button={"payload": "yes", "text": "Ναι"}))

    db.expire_all()
    assert db.get(Introduction, intro_id).pro_confirmed_at is not None
    tap = _rows(PRO)[-1]
    assert (tap.body, tap.introduction_id) == ("Ναι", intro_id)
    assert tap.read_at is not None            # handled, so not waiting to be read
    assert outbox.outbox == []                # nobody emailed
    assert ai.histories == []                 # the assistant stays out of it


def test_a_typed_reply_to_the_follow_up_reaches_staff(
        client, db, professions, follow_up_on_meta, outbox, owner):
    _published(db, professions)
    intro_id = _ask(client)
    outbox.clear()

    _write(client, number=BUYER, text="He has not called yet, can you help?")
    row = _rows(BUYER)[-1]
    assert row.introduction_id == intro_id and row.read_at is None
    assert [m.to for m in outbox.outbox] == [owner.email]


# ── answering, and the 24 hours ─────────────────────────────────────────────
def test_staff_answer_in_their_own_words_within_24_hours(client, inbox, clock, as_caller, caller):
    _write(client)
    clock.advance(timedelta(hours=23, minutes=59))

    response = _reply(client)
    assert response.status_code == 201, response.text
    assert inbox.texts == [(BUYER, "Thanks, we will call you today.")]
    sent = response.json()
    assert (sent["status"], sent["sent_by"], sent["staff_name"]) == ("sent", "staff", caller.name)
    assert _rows()[-1].provider_id == "TX" + "1".zfill(32)


def test_after_24_hours_only_a_template_can_go(client, inbox, clock, as_caller):
    _write(client)
    clock.advance(timedelta(hours=24))

    refused = _reply(client)
    assert refused.status_code == 409
    assert "only allows a template" in refused.json()["detail"]
    assert inbox.texts == []
    assert _thread(client)["open_until"] is None

    assert _template(client).status_code == 201


def test_the_24_hours_run_from_their_last_message(client, inbox, clock, as_caller):
    _write(client, wamid="wamid.1")
    clock.advance(timedelta(hours=20))
    _write(client, wamid="wamid.2", at=clock.now())
    clock.advance(timedelta(hours=10))
    assert _reply(client).status_code == 201
    assert _time(_thread(client)["open_until"]) == START + timedelta(hours=44)


def test_a_reaction_does_not_open_the_24_hours(client, inbox, as_caller):
    _post(client, _message(BUYER, "wamid.r", kind="reaction",
                           reaction={"message_id": "wamid.x", "emoji": "👍"}))
    assert _reply(client).status_code == 409


def test_a_late_callback_counts_from_when_they_wrote(client, inbox, clock, as_caller):
    clock.advance(timedelta(hours=23))
    _write(client, wamid="wamid.late", at=START)          # Meta retried for 23 hours
    assert _time(_thread(client)["open_until"]) == START + timedelta(hours=24)
    clock.advance(timedelta(hours=1))
    assert _reply(client).status_code == 409


def test_a_phone_with_its_clock_ahead_does_not_stretch_the_24_hours(client, inbox, clock, as_caller):
    _write(client, at=START + timedelta(days=3))
    assert _rows()[0].at == START


def test_nothing_goes_when_sending_is_not_set_up(client, inbox, as_caller):
    from app.main import app

    _write(client)
    app.dependency_overrides[get_inbox_sender] = lambda: None
    assert client.get("/api/whatsapp/conversations").json()["sending"] is None
    for response in (_reply(client), _template(client)):
        assert response.status_code == 409
        assert "not set up" in response.json()["detail"]


def test_a_refusal_is_kept_with_metas_reason(client, inbox, as_caller):
    _write(client)
    inbox.failure = WhatsAppResult(False, "Meta refused it (400, error 131047): Re-engagement message")
    response = _reply(client)
    assert response.status_code == 201
    assert (response.json()["status"], response.json()["detail"]) == (
        "failed", "Meta refused it (400, error 131047): Re-engagement message")
    assert _thread(client)["messages"][-1]["status"] == "failed"


def test_delivery_reports_move_a_message_forward_only(client, inbox, as_caller):
    _write(client)
    _reply(client, "First")
    _reply(client, "Second")
    first, second = (r.provider_id for r in _rows()[1:])

    for status in ("delivered", "read", "delivered"):
        _post(client, {"statuses": [{"id": first, "status": status}]})
    _post(client, {"statuses": [{"id": second, "status": "failed", "errors": [
        {"code": 131026, "title": "Message undeliverable"}]}]})

    one, two = _rows()[1:]
    assert one.status == "read"
    assert two.status == "failed"
    assert two.detail == "WhatsApp could not deliver it: Message undeliverable (error 131026)"


def test_a_message_needs_words_and_must_fit(client, inbox, as_caller):
    _write(client)
    assert _reply(client, "").status_code == 422
    assert _reply(client, "   ").status_code == 422
    assert _reply(client, "x" * 4097).status_code == 422
    assert _reply(client, "x" * 4096).status_code == 201


def test_a_number_needs_its_country_code(client, inbox, as_caller):
    _write(client)
    assert _reply(client, number="07700 900123").status_code == 422
    assert _reply(client, number="+44 7700 900123").status_code == 201   # tidied to BUYER
    assert inbox.texts[-1][0] == BUYER


# ── templates ───────────────────────────────────────────────────────────────
def test_a_template_goes_to_anyone_at_any_time(client, inbox, as_caller):
    stranger = "+4915112345678"
    response = _template(client, number=stranger, template="thank_buyer", language="fr",
                         values=["Anna", "Kostas  Papadopoulos\nPartners"])
    assert response.status_code == 201, response.text
    (sent,) = inbox.sent
    assert (sent.to, sent.template, sent.language) == (stranger, "thank_buyer", "fr")
    # One line, as Meta takes a blank.
    assert sent.variables == ("Anna", "Kostas Papadopoulos Partners")
    assert response.json()["text"].startswith("Bonjour Anna, merci pour votre demande")
    assert response.json()["number"] == stranger
    assert _thread(client, stranger)["messages"][0]["kind"] == "template"


def test_a_template_needs_every_blank_in_a_language_it_has(client, inbox, as_caller):
    for problem in (
        {"template": "no_such_template"},
        {"template": "request_to_pro", "language": "fr"},       # English and Greek only
        {"values": ["Sarah"]},
        {"values": ["Sarah", "   "]},
        {"values": ["Sarah", "x" * 301]},
    ):
        assert _template(client, **problem).status_code == 422, problem
    assert inbox.sent == []


def test_the_template_list_has_what_the_form_needs(client, as_caller):
    templates = {t["key"]: t for t in client.get("/api/whatsapp/templates").json()}
    assert set(templates) == {"thank_buyer", "request_to_pro", "remind_pro", "check_buyer"}
    check = templates["check_buyer"]
    assert check["blanks"] == ["the buyer's first name", "the professional's name"]
    assert check["examples"] == ["Sarah", "Kostas Papadopoulos"]
    assert set(check["texts"]) == {"en", "el", "fr"} and check["buttons"] == ["Yes", "No"]


# ── reading, the count, erasing ─────────────────────────────────────────────
def test_the_count_is_people_waiting_until_someone_opens_them(client, inbox, as_caller):
    _write(client, wamid="wamid.1")
    _write(client, wamid="wamid.2")
    _write(client, number="+4915112345678", wamid="wamid.3")
    assert client.get("/api/whatsapp/unread").json() == {"conversations": 2}

    assert _thread(client)["unread"] == 2
    assert client.post(f"/api/whatsapp/conversations/{BUYER}/read").status_code == 204
    assert _thread(client)["unread"] == 0
    assert client.get("/api/whatsapp/unread").json() == {"conversations": 1}
    # What staff send never counts.
    _reply(client)
    assert client.get("/api/whatsapp/unread").json() == {"conversations": 1}


def test_conversations_come_latest_first(client, inbox, clock, as_caller):
    _write(client, number="+4915112345678", wamid="wamid.1")
    clock.advance(timedelta(minutes=5))
    _write(client, wamid="wamid.2", at=START + timedelta(minutes=5))
    numbers = [c["number"] for c in client.get("/api/whatsapp/conversations").json()["items"]]
    assert numbers == [BUYER, "+4915112345678"]


def test_names_come_from_what_vilaow_knows(client, db, professions, follow_up_on_meta, as_caller):
    _published(db, professions)
    intro_id = _ask(client)
    _write(client, number="+4915112345678", wamid="wamid.1", name="Anna K")
    people = {c["number"]: c for c in client.get("/api/whatsapp/conversations").json()["items"]}
    assert (people[PRO]["name"], people[PRO]["role"]) == ("Kostas Papadopoulos", "professional")
    assert people[PRO]["professional_id"] is not None
    assert (people[BUYER]["name"], people[BUYER]["role"], people[BUYER]["introduction_id"]) == (
        "Sarah Mitchell", "buyer", intro_id)
    assert (people["+4915112345678"]["name"], people["+4915112345678"]["role"]) == ("Anna K", None)


def test_only_the_owner_erases_a_conversation(client, inbox, caller, owner):
    _write(client, wamid="wamid.1")
    _write(client, number="+4915112345678", wamid="wamid.2")
    client.post("/api/auth/login", json={"email": caller.email, "password": "caller-pw"})
    assert client.delete(f"/api/whatsapp/conversations/{BUYER}").status_code == 403

    client.post("/api/auth/login", json={"email": owner.email, "password": "owner-pw"})
    assert client.delete(f"/api/whatsapp/conversations/{BUYER}").status_code == 204
    assert [r.number for r in _rows()] == ["+4915112345678"]


def test_erasing_an_introduction_erases_its_messages(
        client, db, professions, follow_up_on_meta, as_owner):
    _published(db, professions)
    intro_id = _ask(client)
    _write(client, number="+4915112345678", wamid="wamid.1")
    assert client.delete(f"/api/introductions/{intro_id}").status_code == 204
    assert [r.number for r in _rows()] == ["+4915112345678"]


def test_the_inbox_is_for_staff_only(client, inbox):
    _write(client)
    for method, path, body in [
        ("get", "/api/whatsapp/conversations", None),
        ("get", f"/api/whatsapp/conversations/{BUYER}", None),
        ("post", f"/api/whatsapp/conversations/{BUYER}/read", None),
        ("delete", f"/api/whatsapp/conversations/{BUYER}", None),
        ("get", "/api/whatsapp/unread", None),
        ("get", "/api/whatsapp/templates", None),
        ("post", "/api/whatsapp/text", {"number": BUYER, "text": "hi"}),
        ("post", "/api/whatsapp/template", {"number": BUYER, "template": "check_buyer",
                                            "language": "en", "values": ["a", "b"]}),
    ]:
        response = getattr(client, method)(path, **({"json": body} if body else {}))
        assert response.status_code == 401, (method, path)
    assert inbox.texts == [] and inbox.sent == [] and _rows()[0].read_at is None


# ── telling staff ───────────────────────────────────────────────────────────
def test_staff_are_emailed_about_a_new_message(client, db, inbox, outbox, owner, caller):
    db.add(Staff(name="Gone", email="gone@vilaow.com", password_hash=hash_password("x"),
                 active=False))
    db.commit()
    _write(client, text="Is there a lawyer in Chania?", name="Sarah")

    assert sorted(m.to for m in outbox.outbox) == [caller.email, owner.email]
    email = outbox.to(owner.email)[0]
    assert email.subject == "New WhatsApp message from Sarah"
    assert "Is there a lawyer in Chania?" in email.text
    assert f"{SITE}/admin/whatsapp?number=%2B447700900123" in email.text


def test_one_email_per_conversation_every_ten_minutes(client, inbox, clock, outbox, owner):
    _write(client, wamid="wamid.1")
    clock.advance(timedelta(minutes=5))
    _write(client, wamid="wamid.2", at=clock.now())
    assert len(outbox.outbox) == 1
    _write(client, number="+4915112345678", wamid="wamid.3", at=clock.now())
    assert len(outbox.outbox) == 2                    # somebody else: their own email
    clock.advance(timedelta(minutes=6))
    _write(client, wamid="wamid.4", at=clock.now())
    assert len(outbox.outbox) == 3


def test_a_name_cannot_break_the_email(client, inbox, outbox, owner):
    _write(client, name="Eve\r\nBcc: everyone@example.com")
    assert outbox.outbox[0].subject == "New WhatsApp message from Eve Bcc: everyone@example.com"


def test_an_email_that_cannot_go_loses_nothing(client, inbox, owner, as_caller):
    from app.main import app

    app.dependency_overrides[get_email_sender] = lambda: NullEmailSender()
    _write(client)
    assert len(_rows()) == 1
    assert client.get("/api/whatsapp/unread").json() == {"conversations": 1}


def test_no_email_for_an_emoji(client, inbox, outbox, owner):
    _post(client, _message(BUYER, "wamid.r", kind="reaction",
                           reaction={"message_id": "wamid.x", "emoji": "👍"}))
    assert outbox.outbox == []


# ── the assistant ───────────────────────────────────────────────────────────
def test_the_assistant_answers_a_typed_question(client, inbox, ai, as_caller):
    _write(client, text="How much are notary fees?")

    assert ai.histories == [[{"role": "user", "content": "How much are notary fees?"}]]
    (to, text), = inbox.texts
    assert to == BUYER
    assert text == ("Notary fees are about 1% of the price. "
                    f"See the Cost Guide ({SITE}/costs).")
    answer = _thread(client)["messages"][-1]
    assert (answer["sent_by"], answer["status"]) == ("assistant", "sent")
    assert client.get("/api/whatsapp/conversations").json()["ai_replies"] is True


def test_the_assistant_reads_the_conversation_so_far(client, inbox, ai, clock):
    _write(client, wamid="wamid.1", text="Hi")
    clock.advance(timedelta(minutes=2))
    _write(client, wamid="wamid.2", text="I am buying in Crete", at=clock.now())
    assert ai.histories[-1] == [
        {"role": "user", "content": "Hi"},
        {"role": "assistant", "content": inbox.texts[0][1]},
        {"role": "user", "content": "I am buying in Crete"},
    ]


def test_the_assistant_leaves_professionals_to_staff(client, db, professions, inbox, ai):
    _published(db, professions)
    _write(client, number=PRO, text="Did you send me a client?")
    assert ai.histories == [] and inbox.texts == []


def test_the_assistant_stays_quiet_once_staff_have_answered(client, inbox, ai, clock, as_caller):
    _write(client, wamid="wamid.1")
    ai_answers = len(inbox.texts)
    _reply(client, "Hi Sarah, this is Maria from Vilaow.")
    clock.advance(timedelta(minutes=1))
    _write(client, wamid="wamid.2", text="Great, thanks", at=clock.now())
    assert len(ai.histories) == ai_answers == 1
    assert inbox.texts[-1][1] == "Hi Sarah, this is Maria from Vilaow."


def test_the_assistant_answers_one_person_twenty_times_a_day_at_most(client, inbox, ai, clock):
    for n in range(AI_PER_DAY + 2):
        clock.advance(timedelta(minutes=1))
        _write(client, wamid=f"wamid.{n}", text=f"Question {n}", at=clock.now())
    assert len(inbox.texts) == AI_PER_DAY


def test_the_assistant_answers_only_words(client, inbox, ai):
    _post(client, _message(BUYER, "wamid.1", kind="image", image={"id": "m"}))
    _post(client, _message(BUYER, "wamid.2", kind="button", button={"payload": "x", "text": "Ok"}))
    assert ai.histories == []


def test_when_the_assistant_cannot_answer_nothing_goes(client, inbox, ai, outbox, owner):
    ai.error = "busy"
    _write(client)
    assert inbox.texts == []
    assert len(_rows()) == 1 and len(outbox.outbox) == 1   # still kept, staff still told


def test_the_assistant_does_not_answer_an_old_message(client, inbox, ai, clock):
    clock.advance(timedelta(hours=25))
    _write(client, at=START)
    assert ai.histories == []


def test_the_assistant_is_off_unless_switched_on(client, inbox, as_caller):
    _write(client)
    assert inbox.texts == []
    assert client.get("/api/whatsapp/conversations").json()["ai_replies"] is False


def test_the_assistant_answers_the_newest_message_only(db, clock):
    """Two messages in quick succession: the first one's turn finds the second
    and leaves the answer to it, so the person gets one answer, not two."""
    sender = MetaSender()
    inbox = InboxService(db, clock=clock, sender=sender)
    first = inbox.receive(number=BUYER, kind="text", text="Hi", provider_id="wamid.1",
                          profile_name=None, at=START)
    inbox.receive(number=BUYER, kind="text", text="Lawyer in Crete?", provider_id="wamid.2",
                  profile_name=None, at=START)
    assert inbox.ai_reply(first.id, assistant=FakeAssistant(), site_url=SITE) is None
    assert sender.texts == []


def test_the_assistant_keeps_its_answer_when_staff_answer_while_it_writes(db, clock):
    """Answering takes seconds. A staff answer sent meanwhile stands alone."""
    sender = MetaSender()
    inbox = InboxService(db, clock=clock, sender=sender)
    message = inbox.receive(number=BUYER, kind="text", text="Lawyer in Crete?",
                            provider_id="wamid.1", profile_name=None, at=START)

    class Overtaken(FakeAssistant):
        def reply(self, history):
            yield {"type": "text", "text": "Kostas in Chania is a lawyer."}
            db.add(InboxMessage(number=BUYER, direction="out", kind="text", body="Maria here.",
                                status="sent", sent_by="staff", at=START))
            db.commit()
            yield {"type": "done"}

    assert inbox.ai_reply(message.id, assistant=Overtaken(), site_url=SITE) is None
    assert sender.texts == []


def test_without_a_sender_the_assistant_does_nothing(db, clock):
    inbox = InboxService(db, clock=clock, sender=None)
    message = inbox.receive(number=BUYER, kind="text", text="Hi", provider_id="wamid.1",
                            profile_name=None, at=START)
    assistant = FakeAssistant()
    assert inbox.ai_reply(message.id, assistant=assistant, site_url=SITE) is None
    assert assistant.histories == []


def test_the_assistants_markdown_becomes_whatsapp_text():
    assert whatsapp_text("See [the Cost Guide](/costs) and **book a call**.", SITE) == (
        f"See the Cost Guide ({SITE}/costs) and *book a call*.")
    assert whatsapp_text("[https://x.test/a](https://x.test/a)", SITE) == "https://x.test/a"
    assert whatsapp_text("[Kostas](/p/kostas)", SITE + "/") == f"Kostas ({SITE}/p/kostas)"
    assert whatsapp_text("[bad](javascript:void)", SITE) == "bad"
    assert whatsapp_text("## Costs\n\n\n\nAbout __1%__.", SITE) == "*Costs*\n\nAbout *1%*."
    long = whatsapp_text("x" * 5000, SITE)
    assert len(long) == 4096 and long.endswith("…")
