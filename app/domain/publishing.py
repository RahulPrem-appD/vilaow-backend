"""Whether a profile may go live, and if not, exactly why.

Four things must hold, and the owner still clicks the button himself:

  1. the listing agreement is signed **and** the email confirmed
  2. a photo is attached
  3. every required field on that profession has an answer
  4. a human looked at it

Clause 5 of the agreement is why the first is not negotiable: it is the
permission to display a person's name and photo at all. Publishing without it
means publishing someone's likeness with no consent on record.

This module is pure. It takes facts and returns a verdict — no session, no
queries, no imports from the web layer. That is what lets the rule be unit
tested directly, and what stops the "is it ready" badge and the endpoint that
publishes from ever drifting apart: both call `evaluate`, and there is nowhere
else for the rule to live.

Fetching those facts is a repository's job (app/repositories/publishing.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Iterable

from app.domain.fields import missing_required


@dataclass(frozen=True)
class PublishFacts:
    """Everything the gate needs, already looked up.

    A frozen dataclass rather than the ORM object on purpose: the rule should
    not be able to lazy-load a relationship and quietly issue a query.
    """

    has_profession: bool
    agreement_signed: bool
    email_confirmed: bool
    has_photo: bool
    required_fields: tuple = ()
    answers: dict[str, Any] = dc_field(default_factory=dict)


@dataclass(frozen=True)
class Readiness:
    ready: bool
    blockers: tuple[str, ...] = ()
    # The keys behind the blockers, so a screen can highlight the right inputs
    # instead of making a caller read prose and guess.
    missing_field_keys: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "ready": self.ready,
            "blockers": list(self.blockers),
            "missing_field_keys": list(self.missing_field_keys),
        }


def evaluate(facts: PublishFacts) -> Readiness:
    """The gate. The only implementation of it."""
    blockers: list[str] = []
    missing_keys: list[str] = []

    if not facts.has_profession:
        blockers.append("No profession set, so there is no form to complete")

    if not facts.agreement_signed:
        blockers.append("The listing agreement has not been signed")
    elif not facts.email_confirmed:
        # Distinguished on purpose: these are different jobs for a caller. One
        # is a phone call, the other is asking someone to check their inbox.
        blockers.append("Signed, but the email address has not been confirmed yet")

    if not facts.has_photo:
        blockers.append("No photo — his signing form calls it the photo buyers will see")

    for missing in missing_required(facts.required_fields, facts.answers):
        missing_keys.append(missing.key)
        blockers.append(f"{missing.label} is required and empty")

    return Readiness(
        ready=not blockers,
        blockers=tuple(blockers),
        missing_field_keys=tuple(missing_keys),
    )


def needs_attention(published: bool, required_fields: Iterable, answers: dict | None) -> list[str]:
    """Required answers missing on an already-published profile.

    The deliberate consequence of "adding a required field never unpublishes
    anyone": nothing disappears from the public site, but the gap has to
    surface somewhere or it is invisible forever.
    """
    if not published:
        return []
    return [f.key for f in missing_required(required_fields, answers)]
