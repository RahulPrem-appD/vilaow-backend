"""Agreements — request and response shapes."""
from __future__ import annotations

from datetime import datetime

from pydantic import EmailStr, Field

from app.models import Stage
from app.schemas.common import ORMModel


# ── agreements ───────────────────────────────────────────────────────────────
class AgreementIssueOut(ORMModel):
    token: str
    professional_id: int
    terms_version: str
    sent_at: datetime
    expires_at: datetime | None
    # Delivery is reported to the caller rather than only logged: they
    # are on the phone and need to know whether to read the link out.
    link: str | None = None
    email_sent: bool | None = None
    email_detail: str | None = None

class AgreementPublicProfessional(ORMModel):
    id: int
    business_name: str
    contact_name: str | None
    phone: str | None
    email: str | None
    city: str | None
    region: str | None
    profession: str | None
    # So the form can say "we already have this" instead of demanding a second
    # copy of a photo the caller took down over the phone.
    photo: str | None = None

class AgreementPublicOut(ORMModel):
    terms_version: str
    # Signing is two steps; these let the page resume at the outstanding one
    # rather than 410-ing anybody who refreshed after signing.
    signed_at: datetime | None = None
    email_verified_at: datetime | None = None
    signed_email: str | None = None
    # The frozen clauses, served so the page renders exactly the words that
    # will be stored against the signature.
    clauses: list[str]
    consent_statement: str
    sent_at: datetime
    expires_at: datetime | None
    professional: AgreementPublicProfessional

class AgreementSignRequest(ORMModel):
    # Lengths, because this is a public endpoint and every one of these is
    # written to a column. Unbounded input reached the database and came back
    # as an opaque 500 mid-signature — cross-origin, so the signer saw only
    # "check your connection" on a form they had just spent minutes on.
    signed_name: str = Field(min_length=1, max_length=160)
    # The drawn signature: an SVG document from the pad. See
    # app/adapters/pdf/agreement.py for the other half of this contract.
    signature_image: str = Field(max_length=200_000)
    profession: str | None = Field(default=None, max_length=120)
    licence: str = Field(max_length=120)
    vat_number: str = Field(max_length=60)
    # EmailStr, not str. This value replaces the address on the professional
    # record and is where the confirmation code is sent, so a blank or
    # malformed one killed delivery permanently: resend reuses the bad address
    # and re-signing is refused as a spent link. A comma-separated pair was
    # accepted too, and would later receive introduction emails carrying a
    # buyer's name, phone and email at both addresses.
    email: EmailStr
    phone: str = Field(max_length=60)
    photo: str | None = Field(default=None, max_length=200)
    # His tick box. Without it there is no agreement, so the server checks it
    # rather than trusting the page to have enforced it.
    agreed: bool = False

class AgreementSignOut(ORMModel):
    professional_id: int
    signed_at: datetime
    stage: Stage
    # Signing alone does not open the publish gate; the address has to be
    # confirmed too. The page uses this to move on to the code step.
    verification_required: bool = True
    code_sent: bool | None = None
    code_sent_to: str | None = None

class AgreementVerifyRequest(ORMModel):
    code: str = Field(min_length=4, max_length=10)

class AgreementVerifyOut(ORMModel):
    professional_id: int
    verified_at: datetime | None
    stage: Stage
    code_sent: bool | None = None
