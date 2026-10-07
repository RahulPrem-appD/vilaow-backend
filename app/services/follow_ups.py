"""The WhatsApp follow-up of an introduction (change request 12).

What the client asked for, step by step:

  1. a buyer asks to be put in touch with a professional;
  2. Vilaow thanks them, straight away;
  3. Vilaow sends their details to the professional, straight away;
  4. some hours later, unless he has said so, it asks him "Did you contact the
     client?" — Yes / No;
  5. Yes: the introduction is marked a success. No, or no answer: he is
     reminded again, each day, up to a limit;
  6. after two days it asks the buyer whether he did, and saves the answer.

"Every step should be saved on the lead with its time."

How:

* Everything is planned up front. The moment an introduction arrives, every
  message is written down as a scheduled step with the time it is due
  (app/models/introduction_step.py). The admin sees what is coming as well as
  what happened, and nothing depends on a timer remembering anything.
* `run_due` sends whatever is due. It runs straight after an introduction
  arrives, for the two immediate messages, and otherwise whenever
  POST /api/follow-ups/run is called — by a scheduler every few minutes, or by
  a person pressing the button in the admin. The same choice as the review
  requests: a visible endpoint, not a thread that could stop quietly.
* A step is checked when it falls due, not when it was planned: a reminder
  for a professional who has already said yes, or an introduction staff have
  closed, is marked not needed instead of being sent.
* Each step is taken and finished in a transaction of its own, its row locked
  and skipped by anyone else looking, so two schedulers running at once cannot
  send the same reminder twice.
* Replies come in through the webhook (app/routers/follow_ups.py) and are
  matched to the question they answer.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.whatsapp import templates
from app.domain.errors import Conflict, Invalid, NotFound
from app.domain.phones import Reach, buyer_whatsapp, professional_whatsapp
from app.models import (
    Event,
    Introduction,
    IntroductionStep,
    IntroOutcome,
    IntroStatus,
    Professional,
)
from app.ports.clock import Clock
from app.ports.whatsapp import WhatsAppMessage, WhatsAppSender

MODES = ("off", "pretend", "twilio")
LANGUAGES = ("en", "el", "fr")
SENT = ("sent", "delivered", "read")
OUTBOUND = ("thank_buyer", "request_to_pro", "remind_pro", "check_buyer")
# What each person is asked, and so what a reply from them can answer.
QUESTIONS = {"professional": ("request_to_pro", "remind_pro"), "buyer": ("check_buyer",)}
# A provider hiccup is tried again this much later, this many times in all.
RETRY_AFTER = timedelta(minutes=15)
MAX_TRIES = 3
# A scheduled step this far past due means nothing is running the clock.
LATE_AFTER = timedelta(minutes=30)

_YES = {"yes", "y", "yeah", "yep", "ναι", "nai", "oui", "si", "sí", "ja"}
_NO = {"no", "n", "nope", "not yet", "όχι", "οχι", "oxi", "ochi", "non", "nein"}


def parse_answer(*candidates: str | None) -> str:
    """"yes", "no" or "other", from a button's payload or what was typed."""
    for raw in candidates:
        word = (raw or "").strip().lower().strip(".!? ")
        if word in _YES:
            return "yes"
        if word in _NO:
            return "no"
    return "other"


@dataclass(frozen=True)
class FollowUpPolicy:
    first_reminder: timedelta = timedelta(hours=4)
    reminder_every: timedelta = timedelta(hours=24)
    max_reminders: int = 3
    buyer_check: timedelta = timedelta(hours=48)


@dataclass(frozen=True)
class FollowUpConfig:
    mode: str = "off"
    policy: FollowUpPolicy = field(default_factory=FollowUpPolicy)
    pro_language: str = "el"


class FollowUpService:
    def __init__(self, db: Session, *, clock: Clock, sender: WhatsAppSender | None,
                 config: FollowUpConfig) -> None:
        self._db = db
        self._clock = clock
        self._sender = sender
        self._config = config

    @property
    def on(self) -> bool:
        return self._config.mode in ("pretend", "twilio") and self._sender is not None

    @property
    def replaces_email(self) -> bool:
        """For real, WhatsApp is the only channel: the client chose "WhatsApp
        only, no email". Pretending sends nothing, so the emails carry on."""
        return self.on and self._config.mode == "twilio"

    # ── planning ────────────────────────────────────────────────────────────
    def start(self, intro: Introduction) -> None:
        if not self.on or intro.steps:
            return
        begun = intro.created_at
        policy = self._config.policy
        plan: list[tuple[str, str, int | None, datetime]] = [
            ("thank_buyer", "buyer", None, begun),
            ("request_to_pro", "professional", None, begun),
        ]
        plan += [
            ("remind_pro", "professional", n,
             begun + policy.first_reminder + (n - 1) * policy.reminder_every)
            for n in range(1, policy.max_reminders + 1)
        ]
        plan.append(("check_buyer", "buyer", None, begun + policy.buyer_check))
        for kind, recipient, attempt, due in plan:
            intro.steps.append(IntroductionStep(
                kind=kind, recipient=recipient, status="scheduled", attempt=attempt,
                due_at=due, tries=0,
            ))
        self._db.commit()

    # ── sending ─────────────────────────────────────────────────────────────
    def run_due(self, *, introduction_id: int | None = None, limit: int = 50) -> dict[str, int]:
        counts: Counter[str] = Counter()
        while self.on and sum(counts.values()) < limit:
            now = self._clock.now()
            query = select(IntroductionStep).where(
                IntroductionStep.status == "scheduled", IntroductionStep.due_at <= now
            )
            if introduction_id is not None:
                query = query.where(IntroductionStep.introduction_id == introduction_id)
            step = self._db.scalar(
                query.order_by(IntroductionStep.due_at, IntroductionStep.id)
                .limit(1).with_for_update(skip_locked=True)
            )
            if step is None:
                break
            counts[self._take(step, now)] += 1
            self._db.commit()
        return {
            "processed": sum(counts.values()),
            **{outcome: counts[outcome]
               for outcome in ("sent", "failed", "skipped", "cancelled", "retrying")},
        }

    def _take(self, step: IntroductionStep, now: datetime) -> str:
        intro = self._db.get(Introduction, step.introduction_id)
        assert intro is not None  # the step goes with its introduction
        reason = self._not_needed(step, intro)
        if reason:
            return self._finish(step, now, "cancelled", reason)

        professional = self._db.get(Professional, intro.professional_id)
        if step.recipient == "buyer":
            reach = buyer_whatsapp(intro.buyer_phone)
        elif professional is None:
            reach = Reach(None, "The professional's record no longer exists")
        else:
            reach = professional_whatsapp(professional.phone)
        if reach.number is None:
            return self._finish(step, now, "skipped", reach.problem)

        assert self._sender is not None  # run_due only runs while on
        step.number = reach.number
        step.transport = self._sender.transport
        step.tries = (step.tries or 0) + 1
        result = self._sender.send(WhatsAppMessage(
            to=reach.number,
            template=step.kind,
            language=self._language(step, intro),
            variables=templates.variables(step.kind, intro, professional),
        ))
        if result.ok:
            step.provider_id = result.provider_id
            # The words are kept only when nothing was sent, so the admin can
            # read what would have gone. Sent for real, they are on the phone.
            return self._finish(step, now, "sent",
                                result.detail if step.transport == "pretend" else None)
        if result.retry and step.tries < MAX_TRIES:
            step.due_at = now + RETRY_AFTER
            step.detail = f"Attempt {step.tries} failed, trying again: {result.detail}"
            return "retrying"
        return self._finish(step, now, "failed", result.detail)

    @staticmethod
    def _not_needed(step: IntroductionStep, intro: Introduction) -> str | None:
        if step.kind == "remind_pro":
            if intro.pro_confirmed_at is not None:
                return "The professional had confirmed contact"
            if intro.status is IntroStatus.closed:
                return "The introduction was closed"
        if step.kind == "check_buyer" and intro.buyer_answer:
            return "The buyer had already answered"
        return None

    def _language(self, step: IntroductionStep, intro: Introduction) -> str:
        if step.recipient == "professional":
            return self._config.pro_language if self._config.pro_language in LANGUAGES else "en"
        return intro.locale if intro.locale in LANGUAGES else "en"

    @staticmethod
    def _finish(step: IntroductionStep, now: datetime, status: str, detail: str | None) -> str:
        step.status = status
        step.done_at = now
        step.detail = detail
        return status

    # ── replies ─────────────────────────────────────────────────────────────
    def record_reply(self, *, number: str, text: str | None, button: str | None,
                     replied_to: str | None, provider_id: str | None) -> IntroductionStep | None:
        """A WhatsApp message from a buyer or a professional.

        Matched to the question it answers: exactly, by the message it replies
        to, when WhatsApp says which; otherwise the newest message sent to that
        number that is still waiting for an answer. None when it answers
        nothing of ours.
        """
        if provider_id:
            seen = self._db.scalar(
                select(IntroductionStep).where(IntroductionStep.provider_id == provider_id))
            if seen is not None:
                return seen   # Twilio retried the webhook: already recorded
        asked = self._question_for(number, replied_to)
        if asked is None:
            return None
        try:
            return self._answer(asked, parse_answer(button, text), text=text, number=number,
                                provider_id=provider_id, transport="twilio")
        except IntegrityError:
            # The same reply, delivered twice at the same moment: the other
            # delivery stored it first, and acted on it.
            self._db.rollback()
            return self._db.scalar(
                select(IntroductionStep).where(IntroductionStep.provider_id == provider_id))

    def _question_for(self, number: str, replied_to: str | None) -> IntroductionStep | None:
        if replied_to:
            step = self._db.scalar(select(IntroductionStep).where(
                IntroductionStep.provider_id == replied_to,
                IntroductionStep.kind.in_(OUTBOUND),
            ))
            if step is not None:
                return step
        recent = self._db.scalars(
            select(IntroductionStep).where(
                IntroductionStep.number == number,
                IntroductionStep.kind.in_(OUTBOUND),
                IntroductionStep.status.in_(SENT),
            ).order_by(IntroductionStep.done_at.desc(), IntroductionStep.id.desc()).limit(20)
        ).all()
        for step in recent:
            if step.kind in QUESTIONS[step.recipient] and self._waiting(step):
                return step
        return recent[0] if recent else None

    def _waiting(self, step: IntroductionStep) -> bool:
        intro = self._db.get(Introduction, step.introduction_id)
        if intro is None:
            return False
        if step.recipient == "professional":
            return intro.pro_confirmed_at is None
        return intro.buyer_answer is None

    def _answer(self, asked: IntroductionStep, answer: str, *, text: str | None,
                number: str | None, provider_id: str | None,
                transport: str) -> IntroductionStep:
        now = self._clock.now()
        intro = self._db.get(Introduction, asked.introduction_id)
        assert intro is not None
        who = asked.recipient
        reply = IntroductionStep(
            kind="pro_replied" if who == "professional" else "buyer_replied",
            recipient=who, status="received", due_at=now, done_at=now, tries=0,
            number=number, transport=transport, provider_id=provider_id, answer=answer,
            detail=templates.clean(text, 1000) if answer == "other" and text else None,
        )
        intro.steps.append(reply)

        # Only an answer to a question moves anything. "Yes" typed under the
        # thank-you message is kept, but it is not an answer to anything.
        if asked.kind in QUESTIONS[who]:
            if who == "professional" and answer == "yes" and intro.pro_confirmed_at is None:
                self._confirmed(intro, now)
            elif who == "buyer" and answer in ("yes", "no") and answer != intro.buyer_answer:
                intro.buyer_answer = answer
                intro.buyer_answered_at = now
                if answer == "no":
                    self._disputed(intro)
        self._db.commit()
        return reply

    def _confirmed(self, intro: Introduction, now: datetime) -> None:
        intro.pro_confirmed_at = now
        # One UPDATE, on what is still scheduled when it runs. A reminder that
        # run_due is sending at this very moment holds its row; this waits for
        # it, finds it sent, and leaves it — rather than writing "cancelled"
        # over a message that went out.
        self._db.execute(
            update(IntroductionStep)
            .where(IntroductionStep.introduction_id == intro.id,
                   IntroductionStep.kind == "remind_pro",
                   IntroductionStep.status == "scheduled")
            .values(status="cancelled", done_at=now, detail="The professional confirmed contact")
            .execution_options(synchronize_session=False)
        )
        if intro.status is not IntroStatus.closed:
            # "The lead is marked as a successful lead on the site." The check
            # with the buyer still goes out, and a "no" from them reopens it.
            intro.status = IntroStatus.closed
            intro.outcome = IntroOutcome.professional_contacted
            intro.closed_at = now
            intro.closed_by_id = None
        self._db.add(Event(
            professional_id=intro.professional_id, actor_label="WhatsApp",
            kind="introduction_confirmed",
            detail=f"introduction #{intro.id}: the professional said they contacted the client",
        ))

    def _disputed(self, intro: Introduction) -> None:
        """The buyer says nobody has been in touch: the promise is being
        broken, which is what the queue is for. Reopened the way staff reopen
        one — the outcome is not known after all — so it is chased.

        From here it is the staff's to settle. The professional's "yes" stays
        on the record beside the buyer's "no", and no more reminders go: his
        answer stopped them, and asking again would only ask the same man the
        same question."""
        if intro.status is IntroStatus.closed:
            intro.status = IntroStatus.new
            intro.outcome = None
            intro.closed_at = None
            intro.closed_by_id = None
        self._db.add(Event(
            professional_id=intro.professional_id, actor_label="WhatsApp",
            kind="introduction_disputed",
            detail=f"introduction #{intro.id}: the buyer said nobody had contacted them",
        ))

    def pretend_reply(self, introduction_id: int, *, who: str, answer: str) -> IntroductionStep:
        """Staff acting out a reply, to try the flow before WhatsApp is connected."""
        if self._config.mode != "pretend":
            raise Conflict("Pretend answers are only for trying the flow while WhatsApp "
                           "is not connected")
        intro = self._db.get(Introduction, introduction_id)
        if intro is None:
            raise NotFound("Introduction not found")
        asked = max(
            (s for s in intro.steps
             if s.recipient == who and s.kind in QUESTIONS.get(who, ()) and s.status in SENT),
            key=lambda s: (s.done_at or s.due_at, s.id), default=None,
        )
        if asked is None:
            raise Invalid(f"Nothing has been sent to the {who} yet")
        return self._answer(asked, answer, text=None, number=asked.number, provider_id=None,
                            transport="pretend")

    # ── delivery reports ────────────────────────────────────────────────────
    def record_delivery(self, *, provider_id: str, status: str, error_code: str | None) -> None:
        step = self._db.scalar(select(IntroductionStep).where(
            IntroductionStep.provider_id == provider_id, IntroductionStep.kind.in_(OUTBOUND)))
        if step is None or step.status == "failed":
            return
        now = self._clock.now()
        rank = {"sent": 1, "delivered": 2, "read": 3}
        if status in ("failed", "undelivered"):
            self._finish(step, now, "failed",
                         f"WhatsApp could not deliver it (Twilio error {error_code or 'unknown'})")
        elif rank.get(status, 0) > rank.get(step.status, 0):
            # Only ever forward: a late "delivered" never undoes "read".
            step.status = status
            step.done_at = now
        else:
            return
        self._db.commit()

    # ── for the admin ───────────────────────────────────────────────────────
    def settings(self) -> dict:
        policy = self._config.policy
        late = self._db.scalar(select(func.count()).select_from(IntroductionStep).where(
            IntroductionStep.status == "scheduled",
            IntroductionStep.due_at < self._clock.now() - LATE_AFTER,
        )) or 0
        return {
            "mode": self._config.mode if self.on else "off",
            "first_reminder_hours": policy.first_reminder.total_seconds() / 3600,
            "reminder_every_hours": policy.reminder_every.total_seconds() / 3600,
            "max_reminders": policy.max_reminders,
            "buyer_check_hours": policy.buyer_check.total_seconds() / 3600,
            "late_steps": late if self.on else 0,
        }
