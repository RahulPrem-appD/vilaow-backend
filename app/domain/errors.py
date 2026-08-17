"""What the domain can refuse, expressed without mentioning HTTP.

Business rules used to raise `HTTPException` from deep inside the call
pipeline, which meant three things at once: the rule could not be tested
without a request, the rule could not be reused from a CLI or a worker, and
the status code — a transport detail — was decided in the same breath as the
business decision.

These are the vocabulary instead. Services raise them; exactly one place
(app/api/errors.py) knows how each maps onto a status code. Adding a rule
never touches the web layer, and changing a status code never touches a rule.
"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """Base for anything the business refuses to do.

    `detail` is written for the person on the other end — a caller on the
    phone, or a professional halfway through signing — not for a log.
    """

    def __init__(self, detail: str, *, context: dict[str, Any] | None = None) -> None:
        self.detail = detail
        self.context = context or {}
        super().__init__(detail)


class NotFound(DomainError):
    """The thing does not exist, or the asker is not allowed to know it does.

    Deliberately one class for both. An unpublished profile and a missing one
    must be indistinguishable from outside, or the API enumerates the pipeline;
    the same is true of a document somebody is probing ids for.
    """


class PermissionDenied(DomainError):
    """Authenticated, but not allowed."""


class Invalid(DomainError):
    """The input does not satisfy a rule.

    `errors` carries per-field detail where there is any, so a form can mark
    the offending inputs instead of printing a sentence above them.
    """

    def __init__(
        self,
        detail: str,
        *,
        errors: list[dict[str, str]] | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        self.errors = errors or []
        super().__init__(detail, context=context)


class Rejected(DomainError):
    """We understood the request; the value supplied is simply wrong.

    Distinct from `Invalid` on purpose. A malformed payload is the client's
    mistake and a badly-typed field can be pointed at; a wrong confirmation
    code is a correct request carrying a wrong secret, and there is nothing to
    highlight. They also answer differently — 400 rather than 422 — which is
    the contract the signing page was already written against.
    """


class Conflict(DomainError):
    """The request makes sense but the current state forbids it.

    Publishing a profile that has not met its gates, closing an introduction
    with no outcome, verifying before signing.
    """


class Gone(DomainError):
    """It existed and is finished: a spent signing link, an expired one."""


class TooMany(DomainError):
    """Rate limited, or out of attempts."""


class StorageFailure(DomainError):
    """The file could not be stored or read back."""
