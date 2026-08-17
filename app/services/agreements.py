"""Issuing, signing and confirming a listing agreement.

All of this used to live inside the HTTP handlers, which meant the rules could
only be exercised through a request and the status codes were decided in the
same breath as the business decisions. Here the rules raise domain errors and
know nothing about HTTP.

Signing is two steps, and both must complete:

    sign()    captures the drawn signature and the details
    verify()  confirms the emailed code

`signed_at` is set by the first, `email_verified_at` by the second, and the
publish gate requires both. A signature nobody confirmed the address for is
evidence of something, but not of who.

The code goes to the same inbox as the signing link, so it is **email
verification, not a second factor** — anyone who can open the link can read the
code. It earns its place by proving the address is real and reachable. Never
describe it as security.
"""
from __future__ import annotations


import secrets
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.email import templates
from app.adapters.urls import PublicUrls
from app.domain import terms as terms_module
from app.domain.errors import Conflict, Gone, Invalid, NotFound, Rejected, TooMany
from app.models import Agreement, Event, Professional, Stage, Staff
from app.ports.clock import Clock
from app.ports.email import EmailSender
from app.services.photos import photo_reference
from app.security import hash_password, verify_password

OTP_TTL = timedelta(minutes=10)
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_COOLDOWN = timedelta(seconds=60)


@dataclass(frozen=True)
class IssuedAgreement:
    agreement: Agreement
    link: str
    email_sent: bool
    email_detail: str


@dataclass(frozen=True)
class SignedAgreement:
    professional: Professional
    agreement: Agreement
    code_sent: bool


class AgreementService:
    def __init__(
        self,
        db: Session,
        *,
        clock: Clock,
        email: EmailSender,
        urls: PublicUrls,
        terms_version: str,
        ttl_days: int,
    ) -> None:
        self._db = db
        self._clock = clock
        self._email = email
        self._urls = urls
        self._terms_version = terms_version
        self._ttl_days = ttl_days

    # ── lookups ─────────────────────────────────────────────────────────────
    def by_token(self, token: str) -> Agreement:
        agreement = self._db.scalar(select(Agreement).where(Agreement.token == token))
        if agreement is None:
            raise NotFound("Agreement not found")
        return agreement

    def for_signing_page(self, token: str) -> tuple[Agreement, Professional]:
        """Readable whether or not it has been signed.

        Signing is two steps and the second happens *after* signed_at is set,
        so refusing a signed agreement here stranded anyone who refreshed the
        tab: they could never enter their code, and the agreement sat at
        signature_sent forever. An expired link that was never signed is still
        gone; a signed one stays readable so the page can resume.
        """
        agreement = self.by_token(token)
        if agreement.signed_at is None and self._expired(agreement):
            raise Gone("This agreement link has expired")
        return agreement, self._professional(agreement)

    def _professional(self, agreement: Agreement) -> Professional:
        professional = self._db.get(Professional, agreement.professional_id)
        if professional is None:
            raise NotFound("Professional not found")
        return professional

    def _expired(self, agreement: Agreement) -> bool:
        return agreement.expires_at is not None and agreement.expires_at < self._clock.now()

    # ── issuing ─────────────────────────────────────────────────────────────
    def issue(self, professional_id: int, *, staff: Staff) -> IssuedAgreement:
        professional = self._db.get(Professional, professional_id)
        if professional is None:
            raise NotFound("Professional not found")

        agreement = Agreement(
            professional_id=professional.id,
            token=secrets.token_urlsafe(32),
            terms_version=self._terms_version,
            # Frozen at issue, not at signing: the link showed these words, and
            # a deploy in between must not change what they agreed to.
            terms_text=terms_module.as_text(self._terms_version),
            expires_at=self._clock.now() + timedelta(days=self._ttl_days),
        )
        self._db.add(agreement)
        professional.stage = Stage.signature_sent
        self._record(professional.id, staff, "agreement_issued")
        self._db.commit()
        self._db.refresh(agreement)

        link = self._urls.agreement(agreement.token)

        # Delivery never undoes the issue. The token is the valuable thing and
        # it now exists; if the mail fails a caller can read the link out. What
        # must not happen is a silent failure, so the outcome is returned.
        if not professional.email:
            return IssuedAgreement(agreement, link, False,
                                   "no email address on this record — capture one, "
                                   "or read the link out")

        result = self._email.send(templates.agreement_invitation(
            to=professional.email,
            name=professional.contact_name or professional.business_name,
            link=link,
            ttl_days=self._ttl_days,
        ))
        self._record(
            professional.id, staff,
            "agreement_emailed" if result.ok else "agreement_email_failed",
            detail=f"{professional.email}: {result.detail}",
        )
        self._db.commit()
        return IssuedAgreement(agreement, link, result.ok, result.detail)

    # ── signing ─────────────────────────────────────────────────────────────
    def sign(
        self,
        token: str,
        *,
        signed_name: str,
        signature_image: str,
        licence: str,
        vat_number: str,
        email: str,
        phone: str,
        profession: str | None,
        photo: str | None,
        agreed: bool,
        ip: str | None,
        user_agent: str | None,
    ) -> SignedAgreement:
        agreement = self.by_token(token)
        if agreement.signed_at is not None:
            raise Gone("This agreement has already been signed")
        if self._expired(agreement):
            raise Gone("This agreement link has expired")
        if not agreed:
            raise Invalid("The agreement box has to be ticked")

        professional = self._professional(agreement)
        now = self._clock.now()

        # The professional's own version wins. A caller pre-filled these from a
        # phone call; the person signing is the one attesting they are true.
        professional.license = licence
        professional.vat_number = vat_number
        professional.phone = phone
        # `email` is deliberately NOT promoted here — see verify(). It is held
        # on the agreement until the code sent to it comes back, because the
        # address the record already has is the one a caller confirmed by
        # phone, and overwriting it with an unproven one costs us the only way
        # left to reach this person if the new address is wrong.
        if signed_name:
            professional.contact_name = signed_name
        if photo:
            professional.photo = photo_reference(self._db, professional, photo)

        agreement.signed_name = signed_name
        agreement.signed_email = email
        agreement.signature_image = signature_image
        agreement.signed_fields = {
            "signed_name": signed_name,
            "profession": profession,
            "licence": licence,
            "vat_number": vat_number,
            "email": email,
            "phone": phone,
            "consent_statement": terms_module.CONSENT_STATEMENT,
        }
        agreement.signed_at = now
        agreement.signed_ip = ip
        agreement.signed_user_agent = user_agent

        # Deliberately NOT Stage.signed yet — the address is unverified, so the
        # publish gate must stay shut. That happens in verify().
        professional.stage = Stage.signature_sent

        self._record(professional.id, None, "agreement_signed",
                     actor_label=signed_name or "professional (public sign)")
        self._db.commit()

        sent = self._issue_code(agreement, professional)
        return SignedAgreement(professional, agreement, sent)

    def verify(self, token: str, code: str) -> tuple[Professional, Agreement]:
        agreement = self.by_token(token)

        if agreement.signed_at is None:
            raise Conflict("Sign the agreement first")
        if agreement.email_verified_at is not None:
            # Idempotent rather than an error: a double submit or a refreshed
            # tab should not look like a failure to the person signing.
            return self._professional(agreement), agreement
        if agreement.otp_hash is None:
            raise Conflict("No code has been sent")
        if agreement.otp_attempts >= OTP_MAX_ATTEMPTS:
            raise TooMany("Too many attempts — ask for a new code")
        if agreement.otp_sent_at is not None and agreement.otp_sent_at + OTP_TTL < self._clock.now():
            raise Gone("That code has expired — ask for a new one")

        if not verify_password(code.strip(), agreement.otp_hash):
            # Count the failure before returning, or the limit means nothing.
            agreement.otp_attempts += 1
            self._db.commit()
            remaining = max(0, OTP_MAX_ATTEMPTS - agreement.otp_attempts)
            raise Rejected(f"That code is not right — {remaining} attempts left")

        professional = self._professional(agreement)
        agreement.email_verified_at = self._clock.now()
        agreement.otp_hash = None  # spent: the code cannot be replayed

        # Now it is proven reachable, so it becomes the record's address.
        if agreement.signed_email:
            professional.email = agreement.signed_email
        professional.stage = Stage.signed

        self._record(professional.id, None, "agreement_email_verified",
                     actor_label=agreement.signed_name or "professional",
                     detail=agreement.signed_email)
        self._db.commit()
        return professional, agreement

    def resend_code(self, token: str) -> tuple[Professional, bool]:
        agreement = self.by_token(token)
        if agreement.signed_at is None:
            raise Conflict("Sign the agreement first")
        if agreement.email_verified_at is not None:
            raise Conflict("This address is already verified")

        # A cooldown, so this cannot be used to mail-bomb the professional.
        if agreement.otp_sent_at is not None:
            wait = agreement.otp_sent_at + OTP_RESEND_COOLDOWN - self._clock.now()
            if wait.total_seconds() > 0:
                raise TooMany(
                    f"Wait {int(wait.total_seconds())}s before asking for another code"
                )

        professional = self._professional(agreement)
        return professional, self._issue_code(agreement, professional)

    def open_for_upload(self, token: str) -> Professional:
        """The professional behind an agreement that may still be signed.

        Signing has no session — the token is the credential — so the photo a
        professional attaches while signing cannot use the staff upload route.
        This gates it on exactly the same conditions as signing itself, so a
        spent or expired link cannot be used to push files at the bucket.
        """
        agreement = self.by_token(token)
        if agreement.signed_at is not None:
            raise Gone("This agreement has already been signed")
        if self._expired(agreement):
            raise Gone("This agreement link has expired")
        return self._professional(agreement)

    def latest_signed_for(self, professional_id: int) -> Agreement:
        agreement = self._db.scalar(
            select(Agreement)
            .where(Agreement.professional_id == professional_id,
                   Agreement.signed_at.is_not(None))
            .order_by(Agreement.signed_at.desc())
        )
        if agreement is None:
            raise NotFound("No signed agreement on file")
        return agreement

    def for_pdf(self, agreement: Agreement) -> tuple[Agreement, Professional]:
        if agreement.signed_at is None:
            raise NotFound("Nothing has been signed yet")
        return agreement, self._professional(agreement)

    # ── internals ───────────────────────────────────────────────────────────
    def _issue_code(self, agreement: Agreement, professional: Professional) -> bool:
        code = f"{secrets.randbelow(1_000_000):06d}"  # CSPRNG, not random.randint
        agreement.otp_hash = hash_password(code)
        agreement.otp_sent_at = self._clock.now()
        agreement.otp_attempts = 0
        self._db.commit()

        to = agreement.signed_email or professional.email
        if not to:
            return False
        return self._email.send(templates.signing_code(
            to=to,
            name=professional.contact_name or professional.business_name,
            code=code,
        )).ok

    def _record(self, professional_id: int, staff: Staff | None, kind: str,
                *, actor_label: str | None = None, detail: str | None = None) -> None:
        self._db.add(Event(
            professional_id=professional_id,
            actor_id=staff.id if staff else None,
            actor_label=actor_label or (staff.name if staff else None),
            kind=kind,
            detail=detail,
        ))
