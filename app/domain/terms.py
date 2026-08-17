"""The listing agreement, as published.

His eight clauses, transcribed verbatim from the signing page he supplied
(client-reference / vilaow-agreement-sign.html). This module is the only source
of them: the API serves these words to the signing page, and freezes the same
words onto the agreement row when it is issued.

Why freeze rather than store a version number alone — the agreement declares
itself "valid and binding" under Greek law, and the PDF is rendered on demand
rather than stored as a file. If the wording is ever revised, a PDF regenerated
years later must still reproduce the clauses that person actually agreed to. A
version string cannot do that on its own once the constant below has moved on.

Revising the terms means adding a new version key, never editing an old one.
Editing an existing entry silently rewrites history for everyone who already
signed it.
"""
from __future__ import annotations

CURRENT_VERSION = "2026-01"

TERMS: dict[str, list[str]] = {
    "2026-01": [
        "Vilaow publishes my professional profile on vilaow.com, free of charge. "
        "Buyers contact me directly; any work is agreed directly between me and the buyer.",

        "The information I provide is true, my professional license is valid, and I will "
        "keep both up to date. Any missing details I will complete before my profile goes live.",

        "Vilaow is a listing platform — not an agency and not a party to my engagements — "
        "and does not guarantee clients or revenue.",

        "Buyer reviews are independent of any payment and cannot be bought, edited or "
        "removed on request.",

        "I allow Vilaow to display my name, photo and profile details on the platform "
        "(GDPR-compliant).",

        "Either side can end the listing with 14 days' written notice. Vilaow may remove "
        "a profile that breaks these terms.",

        "This agreement is valid and binding once signed, even if some details are "
        "completed later.",

        "Greek law applies.",
    ],
}

# Shown beside the tick box on his page, and stored with the signature because
# it is the sentence that makes the signature mean something.
CONSENT_STATEMENT = (
    "I have read the terms above and I agree to them. This electronic signature is "
    "binding, together with today's date, my photo and my details."
)


def clauses(version: str = CURRENT_VERSION) -> list[str]:
    """The numbered clauses for a version, or the current ones if unknown."""
    return TERMS.get(version, TERMS[CURRENT_VERSION])


def as_text(version: str = CURRENT_VERSION) -> str:
    """The clauses as one numbered block, for freezing onto a signature.

    Plain text on purpose: it has to stay readable and comparable decades after
    whatever markup the page happened to use has been rewritten.
    """
    return "\n\n".join(f"{i}. {clause}" for i, clause in enumerate(clauses(version), start=1))
