"""A buyer asking to be put in touch, and the queue that chases it.

Two use cases in one service because they are two ends of the same promise.
The public end has no account behind it, so everything it accepts is untrusted:
submitting the form causes a real person's name, email and phone to be emailed
to a third party. Consent is required *here*, not in the page, and so is the
rate limiting.

The staff end exists because the confirmation email tells the buyer the
professional will call. If he doesn't, it is Vilaow that broke the promise —
which is why this is a worked queue with an overdue clock, not a log.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.email import templates
from app.adapters.urls import PublicUrls
from app.domain.errors import Invalid, NotFound, TooMany
from app.models import (
    Event,
    Introduction,
    IntroOutcome,
    IntroStatus,
    Professional,
    Review,
    ReviewKind,
    Staff,
)
from app.ports.clock import Clock
from app.ports.email import EmailSender

# Enough for a household comparing three lawyers; nowhere near enough to spam
# the directory, or to use the form to harass someone by repeatedly submitting
# their number.
RATE_WINDOW = timedelta(hours=1)
MAX_PER_IP_PER_WINDOW = 5
MAX_PER_EMAIL_PER_WINDOW = 3

# Long enough that the work has actually happened, short enough to be fresh.
REVIEW_DELAY = timedelta(days=3)

CONSENT_TEXT = (
    "I agree that Vilaow may pass my name, email and phone number to the "
    "professional I have chosen, so that they can contact me directly."
)


@dataclass(frozen=True)
class IntroductionRequest:
    slug: str
    buyer_name: str
    buyer_email: str
    buyer_phone: str | None
    message: str | None
    source_page: str | None
    consent: bool
    honeypot: str | None
    ip: str | None
    user_agent: str | None


class IntroductionService:
    def __init__(self, db: Session, *, clock: Clock, email: EmailSender, urls: PublicUrls) -> None:
        self._db = db
        self._clock = clock
        self._email = email
        self._urls = urls

    # ── the public request ──────────────────────────────────────────────────
    def request(self, req: IntroductionRequest) -> Introduction | None:
        professional = self._db.scalar(
            select(Professional).where(
                Professional.slug == req.slug, Professional.published.is_(True)
            )
        )
        if professional is None:
            # Same 404 as the read endpoints: an unpublished record must not be
            # distinguishable from one that does not exist.
            raise NotFound("Not found")

        if req.honeypot:
            # A bot filled the hidden field. Answer as if it worked so the
            # script learns nothing, and store nothing. `None` tells the router
            # to return the same success shape.
            return None

        if not req.consent:
            raise Invalid("We need your permission to pass your details to the professional")

        if self._too_many(req.ip, req.buyer_email):
            raise TooMany("That is a lot of requests in a short time — please try again later")

        now = self._clock.now()
        intro = Introduction(
            professional_id=professional.id,
            # Snapshot: the queue must still read correctly if the profile is
            # later renamed, unpublished or deleted.
            professional_name=professional.contact_name or professional.business_name,
            professional_role=professional.profession.label if professional.profession else None,
            city=professional.city,
            buyer_name=req.buyer_name.strip(),
            buyer_email=req.buyer_email.strip().lower(),
            buyer_phone=(req.buyer_phone or "").strip() or None,
            message=(req.message or "").strip() or None,
            consent_at=now,
            consent_text=CONSENT_TEXT,
            source_page=req.source_page,
            ip=req.ip,
            user_agent=req.user_agent,
            status=IntroStatus.new,
            created_at=now,
        )
        intro.set_due()
        self._db.add(intro)
        self._db.commit()
        self._db.refresh(intro)

        self._notify(intro, professional)
        return intro

    def _notify(self, intro: Introduction, professional: Professional) -> None:
        """Neither email failing may undo the record. A lost email is
        recoverable from the queue; a lost introduction is a buyer who was told
        someone would call and never heard from anyone."""
        if professional.email:
            result = self._email.send(templates.introduction_to_professional(
                to=professional.email,
                name=professional.contact_name or professional.business_name,
                buyer_name=intro.buyer_name,
                buyer_email=intro.buyer_email,
                buyer_phone=intro.buyer_phone,
                message=intro.message,
            ))
            self._db.add(Event(
                professional_id=professional.id,
                actor_label="introduction",
                kind="introduction_emailed" if result.ok else "introduction_email_failed",
                detail=f"{professional.email}: {result.detail}",
            ))

        # Evented like the professional's, and for the same reason. This email
        # is what tells the buyer someone will be in touch, so when a buyer
        # says they never heard anything, the caller needs to see whether it
        # left the building — not guess from the fact that a row exists.
        confirmation = self._email.send(templates.introduction_confirmation(
            to=intro.buyer_email,
            buyer_name=intro.buyer_name,
            professional_name=intro.professional_name or "",
            professional_role=intro.professional_role,
        ))
        self._db.add(Event(
            professional_id=professional.id,
            actor_label="introduction",
            kind="buyer_confirmation_sent" if confirmation.ok
                 else "buyer_confirmation_failed",
            detail=f"{intro.buyer_email}: {confirmation.detail}",
        ))
        self._db.commit()

    def _too_many(self, ip: str | None, email: str) -> bool:
        since = self._clock.now() - RATE_WINDOW
        by_email = self._db.scalar(
            select(func.count()).select_from(Introduction)
            .where(Introduction.buyer_email == email, Introduction.created_at >= since)
        ) or 0
        if by_email >= MAX_PER_EMAIL_PER_WINDOW:
            return True
        if ip:
            by_ip = self._db.scalar(
                select(func.count()).select_from(Introduction)
                .where(Introduction.ip == ip, Introduction.created_at >= since)
            ) or 0
            if by_ip >= MAX_PER_IP_PER_WINDOW:
                return True
        return False

    # ── the caller queue ────────────────────────────────────────────────────
    def get(self, introduction_id: int) -> Introduction:
        intro = self._db.get(Introduction, introduction_id)
        if intro is None:
            raise NotFound("Introduction not found")
        return intro

    def update(self, introduction_id: int, changes: dict, *, staff: Staff) -> Introduction:
        intro = self.get(introduction_id)
        new_status = changes.get("status")

        if new_status is IntroStatus.closed and not (changes.get("outcome") or intro.outcome):
            # Closing without saying what happened is how outcome data quietly
            # becomes worthless — and the outcome is the only thing here that
            # says whether Vilaow works.
            raise Invalid("Say what came of it before closing this introduction")

        for name, value in changes.items():
            setattr(intro, name, value)

        if new_status is IntroStatus.closed and intro.closed_at is None:
            intro.closed_at = self._clock.now()
            intro.closed_by_id = staff.id
        elif new_status is not None and new_status is not IntroStatus.closed:
            # Reopening kept `closed_at` and `outcome`, which is what
            # send_due_review_requests selects on — so an introduction a caller
            # had reopened because the buyer said it was not finished could
            # still send that buyer a "how did it go?" email. Reopening means
            # the outcome is not known yet, so it is not recorded as one.
            intro.closed_at = None
            intro.closed_by_id = None
            intro.outcome = None

        # Only a buyer who went ahead can leave a verified review, so the token
        # is minted here and spent later by the request email.
        if intro.outcome is IntroOutcome.buyer_proceeded and intro.review_token is None:
            intro.review_token = secrets.token_urlsafe(32)

        self._db.add(Event(
            professional_id=intro.professional_id,
            actor_id=staff.id,
            actor_label=staff.name,
            kind="introduction_updated",
            detail=f"status={intro.status.value} "
                   f"outcome={intro.outcome.value if intro.outcome else '—'}",
        ))
        self._db.commit()
        self._db.refresh(intro)
        return intro

    def erase(self, introduction_id: int, *, staff: Staff) -> None:
        """Erasure of a buyer's enquiry.

        Nothing expires on its own — that is the stated retention policy — so
        the right to erasure has to be a button that genuinely works.

        A verified review left through this introduction loses its link but
        survives: clause 4 says reviews cannot be removed on request, and the
        review is the professional's record, not the buyer's personal data.
        The author is already shortened to "Sarah M." for exactly this reason.
        """
        intro = self.get(introduction_id)
        professional_id = intro.professional_id
        email = intro.buyer_email

        self._db.delete(intro)

        # Deleting the row was not erasure. Every email sent about this
        # introduction wrote the buyer's address into `events.detail` — three
        # of them — so after "deleting" the enquiry the address was still
        # sitting in the audit trail, indefinitely, which is precisely what the
        # privacy page promises does not happen.
        #
        # The events themselves stay: they are the record that Vilaow acted,
        # which is the professional's history and not the buyer's personal
        # data. Only the address is redacted out of them.
        self._redact(professional_id, email)

        self._db.add(Event(
            professional_id=professional_id,
            actor_id=staff.id,
            actor_label=staff.name,
            kind="introduction_erased",
            detail=f"introduction #{introduction_id} deleted at request",
        ))
        self._db.commit()

    def _redact(self, professional_id: int, email: str | None) -> None:
        """Replace a buyer's address wherever it was written into event text."""
        if not email:
            return
        rows = self._db.scalars(
            select(Event).where(Event.professional_id == professional_id,
                                Event.detail.contains(email))
        ).all()
        for event in rows:
            event.detail = (event.detail or "").replace(email, "[erased]")

    # ── asking for the review ───────────────────────────────────────────────
    def send_due_review_requests(self) -> dict:
        """Deliberately callable rather than a background timer: this project
        has no scheduler, and an endpoint can be driven by cron, by a button or
        by a person — all visible, unlike a thread that quietly stopped."""
        cutoff = self._clock.now() - REVIEW_DELAY
        due = self._db.scalars(
            select(Introduction).where(
                Introduction.outcome == IntroOutcome.buyer_proceeded,
                Introduction.closed_at.is_not(None),
                Introduction.closed_at <= cutoff,
                Introduction.review_requested_at.is_(None),
                Introduction.review_token.is_not(None),
            )
        ).all()

        sent = 0
        for intro in due:
            result = self._email.send(templates.review_request(
                to=intro.buyer_email,
                buyer_name=intro.buyer_name,
                professional_name=intro.professional_name or "",
                link=self._urls.review(intro.review_token or ""),
            ))
            self._db.add(Event(
                professional_id=intro.professional_id,
                actor_label="review request",
                kind="review_requested" if result.ok else "review_request_failed",
                detail=f"{intro.buyer_email}: {result.detail}",
            ))
            if result.ok:
                # Left unset on failure on purpose, so the next run retries.
                intro.review_requested_at = self._clock.now()
                sent += 1
        self._db.commit()
        return {"due": len(due), "sent": sent}


class VerifiedReviewService:
    """Reviews left by a buyer Vilaow can show it introduced.

    Separate from the queue service because it is a different actor doing a
    different job: a buyer with a one-time token, not a caller with a session.
    """

    def __init__(self, db: Session, *, clock: Clock) -> None:
        self._db = db
        self._clock = clock

    def _by_token(self, token: str) -> Introduction:
        intro = self._db.scalar(select(Introduction).where(Introduction.review_token == token))
        # Only a buyer who went ahead may leave one — that is exactly what
        # makes the review verified rather than an anonymous submission.
        if intro is None or intro.outcome is not IntroOutcome.buyer_proceeded:
            raise NotFound("Not found")
        return intro

    def context(self, token: str) -> Introduction:
        return self._by_token(token)

    def submit(self, token: str, *, stars: int, text: str | None) -> None:
        from app.domain.errors import Conflict

        intro = self._by_token(token)
        if intro.review_submitted_at is not None:
            raise Conflict("You have already left a review")

        now = self._clock.now()
        self._db.add(Review(
            professional_id=intro.professional_id,
            kind=ReviewKind.vilaow_verified,
            introduction_id=intro.id,
            author=short_author(intro.buyer_name),
            stars=stars,
            text=(text or "").strip() or None,
            context=f"Introduced by Vilaow, {now.strftime('%B %Y')}",
            source="Vilaow buyer",
        ))
        intro.review_submitted_at = now
        # There is deliberately no edit or delete path from here. Clause 4 says
        # reviews "cannot be bought, edited or removed on request", and that is
        # only true if the code makes it true.
        self._db.add(Event(
            professional_id=intro.professional_id,
            actor_label=short_author(intro.buyer_name),
            kind="verified_review_left",
            detail=f"{stars} stars",
        ))
        self._db.commit()


def short_author(name: str) -> str:
    """"Sarah Mitchell" -> "Sarah M." — his existing review format, and a
    smaller amount of a real person's identity to publish forever."""
    parts = [p for p in (name or "").split() if p]
    if not parts:
        return "A buyer"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0].upper()}."
