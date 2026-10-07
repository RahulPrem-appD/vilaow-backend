"""The WhatsApp follow-up of an introduction (change request 12).

What the client asked for, as tests: the buyer is thanked and the professional
gets their details straight away; he is reminded until he says yes, three times
at most; his "yes" marks a success; the buyer is asked after two days, and a
"no" from them reopens the introduction; every step is kept with its time.

And what makes it safe to leave running: a reply counts only if Twilio signed
it, a scheduler needs the jobs key, no step is sent twice, and nothing is
guessed — a number that cannot be reached is skipped, never sent somewhere.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.adapters.whatsapp.pretend import PretendWhatsAppSender, RecordingWhatsAppSender
from app.adapters.whatsapp.twilio import signature
from app.api.deps import (
    TwilioWebhook,
    get_clock,
    get_follow_up_config,
    get_settings,
    get_twilio_webhook,
    get_whatsapp_sender,
)
from app.db import SessionLocal
from app.models import Event, Introduction, IntroductionStep, IntroStatus, Professional, Stage
from app.ports.clock import FrozenClock
from app.ports.whatsapp import WhatsAppResult
from app.services.follow_ups import FollowUpConfig

START = datetime(2026, 10, 7, 9, 0, tzinfo=timezone.utc)
HOOK = TwilioWebhook(auth_token="test-token", base="https://api.vilaow.test")
PRO = "+306912345678"
BUYER = "+447700900123"


# ── set-up ──────────────────────────────────────────────────────────────────
@pytest.fixture
def clock():
    from app.main import app

    frozen = FrozenClock(START)
    app.dependency_overrides[get_clock] = lambda: frozen
    yield frozen
    app.dependency_overrides.pop(get_clock, None)


def _switch_on(mode: str, sender):
    from app.main import app

    app.dependency_overrides[get_whatsapp_sender] = lambda: sender
    app.dependency_overrides[get_follow_up_config] = lambda: FollowUpConfig(mode=mode)
    app.dependency_overrides[get_twilio_webhook] = lambda: HOOK if mode == "twilio" else None


@pytest.fixture
def whatsapp(clock):
    """WhatsApp switched on for real, with a sender that keeps what it is given."""
    from app.main import app

    sender = RecordingWhatsAppSender()
    _switch_on("twilio", sender)
    yield sender
    app.dependency_overrides.pop(get_twilio_webhook, None)


@pytest.fixture
def pretend(clock):
    from app.main import app

    _switch_on("pretend", PretendWhatsAppSender())
    yield
    app.dependency_overrides.pop(get_twilio_webhook, None)


def _published(db, professions, **kw):
    base = dict(
        business_name="Papadopoulos & Partners", contact_name="Kostas Papadopoulos",
        email="kostas@example.com", phone="691 234 5678",
        city="Heraklion", region="Crete", profession_id=professions["lawyer"],
        slug="kostas", published=True, stage=Stage.signed, published_at=START,
    )
    base.update(kw)
    professional = Professional(**base)
    db.add(professional)
    db.commit()
    return professional


def _ask(client, **kw) -> int:
    body = {
        "slug": "kostas", "buyer_name": "Sarah Mitchell", "buyer_email": "sarah@example.com",
        "buyer_phone": "+44 7700 900123", "message": "Buying a house near Chania in the spring.",
        "consent": True,
    }
    body.update(kw)
    response = client.post("/api/public/introductions", json=body)
    assert response.status_code == 201, response.text
    return _latest_intro_id()


def _latest_intro_id() -> int:
    with SessionLocal() as s:
        return s.scalar(select(func.max(Introduction.id)))


def _steps(db) -> list[IntroductionStep]:
    db.expire_all()
    return list(db.scalars(select(IntroductionStep).order_by(IntroductionStep.id)))


def _step(db, kind: str, attempt: int | None = None) -> IntroductionStep:
    return next(s for s in _steps(db) if s.kind == kind and (attempt is None or s.attempt == attempt))


def _intro(db) -> Introduction:
    db.expire_all()
    return db.scalar(select(Introduction))


def _events(db) -> list[str]:
    return [e.kind for e in db.scalars(select(Event))]


def _with_jobs_secret(value: str):
    """Settings as they are, with this jobs secret. A function of no
    arguments: FastAPI would read any parameter as a query string field."""
    def override():
        return get_settings().model_copy(update={"jobs_secret": value})
    return override


def _run(staff_client) -> dict:
    response = staff_client.post("/api/follow-ups/run")
    assert response.status_code == 200, response.text
    return response.json()


def _signed_post(client, path: str, fields: dict[str, str]):
    sig = signature(HOOK.auth_token, HOOK.base + path, {k: [v] for k, v in fields.items()})
    return client.post(path, data=fields, headers={"X-Twilio-Signature": sig})


def _reply(client, number: str, *, body: str = "", button: str | None = None,
           replied_to: str | None = None, sid: str = "SMreply0001"):
    fields = {"From": f"whatsapp:{number}", "To": "whatsapp:+302100000000",
              "MessageSid": sid, "Body": body}
    if button:
        fields["ButtonPayload"] = button
    if replied_to:
        fields["OriginalRepliedMessageSid"] = replied_to
    response = _signed_post(client, "/api/whatsapp/twilio/inbound", fields)
    assert response.status_code == 200, response.text
    return response


def _delivery(client, sid: str, status: str, error: str | None = None):
    fields = {"MessageSid": sid, "MessageStatus": status}
    if error:
        fields["ErrorCode"] = error
    response = _signed_post(client, "/api/whatsapp/twilio/status", fields)
    assert response.status_code == 204, response.text


# ── off until switched on ───────────────────────────────────────────────────
def test_while_it_is_off_nothing_is_planned_and_the_emails_go(client, db, professions, outbox):
    _published(db, professions)
    _ask(client)
    assert _steps(db) == []
    assert len(outbox.outbox) == 2


# ── the plan, and the first two messages ────────────────────────────────────
def test_both_hear_at_once_and_the_rest_is_planned_with_its_time(
    client, db, professions, whatsapp, outbox,
):
    _published(db, professions)
    _ask(client)

    assert [(s.kind, s.status, s.attempt, s.due_at - START) for s in _steps(db)] == [
        ("thank_buyer", "sent", None, timedelta(0)),
        ("request_to_pro", "sent", None, timedelta(0)),
        ("remind_pro", "scheduled", 1, timedelta(hours=4)),
        ("remind_pro", "scheduled", 2, timedelta(hours=28)),
        ("remind_pro", "scheduled", 3, timedelta(hours=52)),
        ("check_buyer", "scheduled", None, timedelta(hours=48)),
    ]
    to_buyer, to_pro = whatsapp.sent
    assert (to_buyer.to, to_buyer.template, to_buyer.language) == (BUYER, "thank_buyer", "en")
    assert to_buyer.variables == ("Sarah", "Kostas Papadopoulos")
    assert (to_pro.to, to_pro.template, to_pro.language) == (PRO, "request_to_pro", "el")
    assert to_pro.variables == ("Kostas", "Sarah Mitchell", "+44 7700 900123",
                                "sarah@example.com", "Buying a house near Chania in the spring.")
    # "WhatsApp only, no email" — once WhatsApp is real, the emails stop.
    assert outbox.outbox == []


def test_the_buyer_hears_in_the_language_of_the_page(client, db, professions, whatsapp):
    _published(db, professions)
    _ask(client, locale="fr")
    _ask(client, locale="de", buyer_email="anna@example.com")   # not a site language
    buyer_messages = [m.language for m in whatsapp.sent if m.template == "thank_buyer"]
    assert buyer_messages == ["fr", "en"]


# ── reminders ───────────────────────────────────────────────────────────────
def test_a_reminder_goes_when_it_is_due_and_not_before(
    client, db, professions, whatsapp, clock, as_caller,
):
    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=3, minutes=59))
    assert _run(as_caller)["sent"] == 0
    clock.advance(timedelta(minutes=1))
    assert _run(as_caller)["sent"] == 1
    assert whatsapp.sent[-1].template == "remind_pro"
    assert _step(db, "remind_pro", 1).status == "sent"


def test_yes_stops_the_reminders_and_marks_a_success(
    client, db, professions, whatsapp, clock, as_caller,
):
    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=4))
    _run(as_caller)

    _reply(client, PRO, button="yes", replied_to=_step(db, "remind_pro", 1).provider_id)

    intro = _intro(db)
    assert intro.pro_confirmed_at == START + timedelta(hours=4)
    assert intro.status is IntroStatus.closed
    assert intro.outcome.value == "professional_contacted"
    assert [s.status for s in _steps(db) if s.kind == "remind_pro"] == ["sent", "cancelled", "cancelled"]
    reply = _step(db, "pro_replied")
    assert (reply.answer, reply.status, reply.recipient) == ("yes", "received", "professional")
    assert "introduction_confirmed" in _events(db)

    # The check with the buyer still goes, two days in — and no more reminders.
    clock.advance(timedelta(days=5))
    _run(as_caller)
    assert _step(db, "check_buyer").status == "sent"
    assert [m.template for m in whatsapp.sent].count("remind_pro") == 1


def test_no_or_silence_reminds_three_times_and_no_more(
    client, db, professions, whatsapp, clock, as_caller,
):
    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=4))
    _run(as_caller)
    _reply(client, PRO, button="no", replied_to=_step(db, "remind_pro", 1).provider_id)
    assert _intro(db).pro_confirmed_at is None

    for _ in range(10):   # ten days of the clock running every few hours
        clock.advance(timedelta(hours=24))
        _run(as_caller)
    assert [m.template for m in whatsapp.sent].count("remind_pro") == 3
    assert _intro(db).status is IntroStatus.new


def test_the_buyer_check_keeps_the_answer_and_a_no_reopens_it(
    client, db, professions, whatsapp, clock, as_caller,
):
    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=4))
    _run(as_caller)
    _reply(client, PRO, button="yes", replied_to=_step(db, "remind_pro", 1).provider_id)
    assert _intro(db).status is IntroStatus.closed

    clock.advance(timedelta(hours=44))
    _run(as_caller)
    _reply(client, BUYER, button="no", replied_to=_step(db, "check_buyer").provider_id,
           sid="SMbuyer0001")

    intro = _intro(db)
    assert (intro.buyer_answer, intro.buyer_answered_at) == ("no", START + timedelta(hours=48))
    # The professional said yes, the buyer says no: back in the queue to chase.
    assert intro.status is IntroStatus.new
    assert intro.outcome is None and intro.closed_at is None
    assert "introduction_disputed" in _events(db)


# ── matching replies ────────────────────────────────────────────────────────
def test_a_typed_answer_is_matched_by_the_number_it_came_from(
    client, db, professions, whatsapp, clock, as_caller,
):
    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=4))
    _run(as_caller)
    _reply(client, PRO, body="Ναι")   # typed, and WhatsApp did not say what it answers
    assert _intro(db).pro_confirmed_at is not None


def test_a_message_that_answers_nothing_of_ours_changes_nothing(client, db, whatsapp):
    _reply(client, "+15550001111", body="hello?")
    assert _steps(db) == []


def test_the_same_reply_delivered_twice_counts_once(
    client, db, professions, whatsapp, clock, as_caller,
):
    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=4))
    _run(as_caller)
    sid = _step(db, "remind_pro", 1).provider_id
    _reply(client, PRO, button="no", replied_to=sid, sid="SMdup")
    _reply(client, PRO, button="no", replied_to=sid, sid="SMdup")   # Twilio retried
    assert len([s for s in _steps(db) if s.kind == "pro_replied"]) == 1


# ── what is checked when a step falls due ───────────────────────────────────
def test_a_closed_introduction_is_not_chased(
    client, db, professions, whatsapp, clock, as_caller,
):
    _published(db, professions)
    intro_id = _ask(client)
    as_caller.patch(f"/api/introductions/{intro_id}",
                    json={"status": "closed", "outcome": "buyer_went_elsewhere"})
    clock.advance(timedelta(hours=4))
    assert _run(as_caller)["cancelled"] == 1
    reminder = _step(db, "remind_pro", 1)
    assert (reminder.status, reminder.detail) == ("cancelled", "The introduction was closed")


def test_numbers_that_cannot_be_reached_are_skipped_not_guessed(client, db, professions, whatsapp):
    _published(db, professions, phone="210 123 4567")     # a Greek landline
    _ask(client, buyer_phone="07700 900123")              # no country code
    thanks, request = _steps(db)[:2]
    assert thanks.status == "skipped" and "country code" in thanks.detail
    assert request.status == "skipped" and "landline" in request.detail
    assert whatsapp.sent == []


# ── failures ────────────────────────────────────────────────────────────────
def test_a_provider_hiccup_is_tried_three_times_then_reported(
    client, db, professions, whatsapp, clock, as_caller,
):
    whatsapp.failure = WhatsAppResult(False, "Twilio refused it (503, error ?)", retry=True)
    _published(db, professions)
    _ask(client)
    thanks = _step(db, "thank_buyer")
    assert (thanks.status, thanks.tries, thanks.due_at) == ("scheduled", 1, START + timedelta(minutes=15))
    assert "trying again" in thanks.detail

    clock.advance(timedelta(minutes=15))
    _run(as_caller)
    assert _step(db, "thank_buyer").tries == 2
    clock.advance(timedelta(minutes=15))
    _run(as_caller)
    thanks = _step(db, "thank_buyer")
    assert (thanks.status, thanks.tries) == ("failed", 3)


def test_a_refusal_is_reported_at_once(client, db, professions, whatsapp):
    whatsapp.failure = WhatsAppResult(False, "Twilio refused it (400, error 63003)", retry=False)
    _published(db, professions)
    _ask(client)
    thanks = _step(db, "thank_buyer")
    assert thanks.status == "failed" and "63003" in thanks.detail


def test_a_sender_that_breaks_never_fails_the_buyers_request(client, db, professions, whatsapp):
    class Broken:
        transport = "twilio"

        def send(self, message):
            raise RuntimeError("boom")

    _switch_on("twilio", Broken())
    _published(db, professions)
    _ask(client)          # 201: the request stands
    assert _intro(db) is not None
    # Planned, waiting for the next run to try again.
    assert {s.status for s in _steps(db)} == {"scheduled"}


def test_a_step_another_run_is_busy_with_is_skipped_not_sent_twice(
    client, db, professions, whatsapp, clock, as_caller,
):
    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=4))
    reminder_id = _step(db, "remind_pro", 1).id

    with SessionLocal() as other_run:
        other_run.execute(select(IntroductionStep).where(IntroductionStep.id == reminder_id)
                          .with_for_update())
        assert _run(as_caller)["sent"] == 0       # locked elsewhere: left alone
        other_run.rollback()
    assert _run(as_caller)["sent"] == 1
    assert [m.template for m in whatsapp.sent].count("remind_pro") == 1


# ── Twilio's callbacks ──────────────────────────────────────────────────────
def test_a_callback_twilio_did_not_sign_is_refused(client, db, whatsapp):
    fields = {"From": f"whatsapp:{PRO}", "Body": "yes", "MessageSid": "SMforged"}
    path = "/api/whatsapp/twilio/inbound"
    assert client.post(path, data=fields).status_code == 403
    assert client.post(path, data=fields, headers={"X-Twilio-Signature": "bogus"}).status_code == 403
    # Signed for another address: a signature cannot be lifted from elsewhere.
    lifted = signature(HOOK.auth_token, "https://elsewhere.test" + path,
                       {k: [v] for k, v in fields.items()})
    assert client.post(path, data=fields, headers={"X-Twilio-Signature": lifted}).status_code == 403


def test_twilios_addresses_do_not_exist_until_whatsapp_is_real(client, pretend):
    assert client.post("/api/whatsapp/twilio/inbound", data={"Body": "x"}).status_code == 404
    assert client.post("/api/whatsapp/twilio/status", data={"MessageSid": "x"}).status_code == 404


def test_delivery_reports_only_move_a_step_forward(client, db, professions, whatsapp):
    _published(db, professions)
    _ask(client)
    request_sid = _step(db, "request_to_pro").provider_id
    _delivery(client, request_sid, "read")
    _delivery(client, request_sid, "delivered")       # late, and out of order
    assert _step(db, "request_to_pro").status == "read"

    _delivery(client, _step(db, "thank_buyer").provider_id, "undelivered", error="63016")
    thanks = _step(db, "thank_buyer")
    assert thanks.status == "failed" and "63016" in thanks.detail


# ── running it, and seeing it ───────────────────────────────────────────────
def test_running_due_steps_needs_a_login_or_the_jobs_key(client, whatsapp):
    from app.main import app

    path = "/api/follow-ups/run"
    assert client.post(path).status_code == 401

    secret = "s" * 40
    for configured in ("", secret):
        app.dependency_overrides[get_settings] = _with_jobs_secret(configured)
        try:
            # An empty secret never matches, even an empty key.
            assert client.post(path, headers={"X-Jobs-Key": ""}).status_code == 403
            assert client.post(path, headers={"X-Jobs-Key": "guess"}).status_code == 403
        finally:
            app.dependency_overrides.pop(get_settings, None)

    app.dependency_overrides[get_settings] = _with_jobs_secret(secret)
    try:
        response = client.post(path, headers={"X-Jobs-Key": secret})
        assert response.status_code == 200 and response.json()["processed"] == 0
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_the_admin_sees_every_step_with_its_time(client, db, professions, whatsapp, as_caller):
    _published(db, professions)
    _ask(client, locale="el")
    item = as_caller.get("/api/introductions").json()["items"][0]
    assert item["locale"] == "el"
    assert [s["kind"] for s in item["steps"]] == [
        "thank_buyer", "request_to_pro", "remind_pro", "remind_pro", "remind_pro", "check_buyer",
    ]
    assert item["steps"][0]["status"] == "sent" and item["steps"][0]["done_at"]
    assert item["steps"][2]["status"] == "scheduled" and item["steps"][2]["due_at"]


def test_the_settings_say_how_it_runs_and_whether_the_clock_is_late(
    client, db, professions, whatsapp, clock, as_caller,
):
    assert as_caller.get("/api/follow-ups/settings").json() == {
        "mode": "twilio", "first_reminder_hours": 4.0, "reminder_every_hours": 24.0,
        "max_reminders": 3, "buyer_check_hours": 48.0, "late_steps": 0,
    }
    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=4, minutes=31))   # a reminder half an hour overdue
    assert as_caller.get("/api/follow-ups/settings").json()["late_steps"] == 1


def test_erasing_an_introduction_erases_its_whatsapp_trail(client, db, professions, whatsapp, as_owner):
    _published(db, professions)
    intro_id = _ask(client)
    assert as_owner.delete(f"/api/introductions/{intro_id}").status_code == 204
    assert _steps(db) == []


# ── pretend mode ────────────────────────────────────────────────────────────
def test_pretending_writes_down_what_it_would_say_and_sends_nothing(
    client, db, professions, pretend, outbox,
):
    _published(db, professions)
    _ask(client)
    thanks = _step(db, "thank_buyer")
    assert (thanks.status, thanks.transport) == ("sent", "pretend")
    assert thanks.detail == ("Would say: Hi Sarah, thank you for your request on Vilaow. We have "
                             "passed your details to Kostas Papadopoulos, who will contact you soon.")
    # Nothing really went out, so the emails carry on.
    assert len(outbox.outbox) == 2


def test_staff_can_act_out_the_answers_while_pretending(client, db, professions, pretend, as_caller):
    _published(db, professions)
    intro_id = _ask(client)
    path = f"/api/introductions/{intro_id}/pretend-reply"

    # Nothing has asked the buyer anything yet.
    assert as_caller.post(path, json={"who": "buyer", "answer": "yes"}).status_code == 422

    response = as_caller.post(path, json={"who": "professional", "answer": "yes"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["pro_confirmed_at"] and body["status"] == "closed"
    assert (body["steps"][-1]["kind"], body["steps"][-1]["transport"]) == ("pro_replied", "pretend")


def test_pretend_answers_are_refused_once_whatsapp_is_real(client, db, professions, whatsapp, as_caller):
    _published(db, professions)
    intro_id = _ask(client)
    response = as_caller.post(f"/api/introductions/{intro_id}/pretend-reply",
                              json={"who": "professional", "answer": "yes"})
    assert response.status_code == 409


# ── what the independent review found ───────────────────────────────────────
def test_when_whatsapp_cannot_reach_the_professional_the_email_still_goes(
    client, db, professions, whatsapp, outbox,
):
    _published(db, professions, phone="210 123 4567")     # a landline: no WhatsApp
    _ask(client)
    assert _step(db, "request_to_pro").status == "skipped"
    # WhatsApp only, except where it cannot go at all: he gets the email...
    assert [m.to for m in outbox.outbox] == ["kostas@example.com"]
    # ...and the buyer, whom WhatsApp did reach, gets no email on top.
    assert [m.template for m in whatsapp.sent] == ["thank_buyer"]
    assert "introduction_emailed" in _events(db)


def test_when_whatsapp_cannot_reach_the_buyer_their_confirmation_is_emailed(
    client, db, professions, whatsapp, outbox,
):
    _published(db, professions)
    _ask(client, buyer_phone="07700 900123")              # no country code
    assert _step(db, "thank_buyer").status == "skipped"
    assert [m.to for m in outbox.outbox] == ["sarah@example.com"]
    assert [m.template for m in whatsapp.sent] == ["request_to_pro"]


def test_a_reminder_sent_at_the_moment_of_the_yes_is_not_recorded_as_cancelled(
    client, db, professions, whatsapp, clock, as_caller,
):
    import threading
    import time

    _published(db, professions)
    _ask(client)
    clock.advance(timedelta(hours=4))
    _run(as_caller)                                        # reminder 1 goes
    clock.advance(timedelta(hours=24))
    second = _step(db, "remind_pro", 2)

    replies: list[int] = []
    with SessionLocal() as sending:
        # Another run is sending reminder 2 right now: it holds the row.
        step = sending.get(IntroductionStep, second.id, with_for_update=True)
        step.status, step.done_at, step.provider_id = "sent", START + timedelta(hours=28), "SMsecond"
        answer = threading.Thread(target=lambda: replies.append(
            _reply(client, PRO, body="yes").status_code))
        answer.start()
        time.sleep(1)                                      # the "yes" waits on the row
        sending.commit()
        answer.join(15)

    assert replies == [200]
    statuses = [s.status for s in _steps(db) if s.kind == "remind_pro"]
    assert statuses == ["sent", "sent", "cancelled"]       # sent stays sent
    assert _intro(db).pro_confirmed_at is not None
