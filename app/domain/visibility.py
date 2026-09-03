"""Which built-in fields a public page may show: the vocabulary, and the rule.

The owner-defined form always had its lever — the per-field `public` flag on
each question. The built-in fields had none: a profile showed its photo, its
bio, its rating whenever they had a value, and nothing could turn one off.
This module is that lever, expressed as an allow list rather than a hide list
so that a field nobody has thought about carries a decision already made.

Two levels, the narrower winning. A profession holds the default for everyone
in it — "all lawyers show these fields" — and a professional can decide for
itself, overriding its profession in either direction: showing a field its
profession hides, or hiding one it shows.

`phone`, `email`, `address` and `notes` are deliberately not keys and must
never become them. A buyer never gets a direct contact route — the business is
that Vilaow makes the introduction, and the suite holds that line in
test_the_direct_phone_number_is_never_published. An allow list is what keeps
it held, because a key that does not exist cannot be switched on; adding one
of those four here would be the one edit that quietly breaks the product.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import Sequence

from app.domain.errors import Invalid

# The closed vocabulary, in the order the page renders. Every key names one
# thing a buyer can be shown; the comment under each says which thing, and the
# ones that carry more than their own column especially.
FIELD_KEYS: tuple[str, ...] = (
    "photo",          # the headshot, on the card and the profile alike
    "subrole",        # the line under the name — "Conveyancing lawyer"
    "coverage",       # where they work — "All of Crete"
    "verified_year",  # the "Verified by Vilaow · 2026" chip
    "bio",            # the paragraph about them
    "languages",      # the languages they work in
    "highlights",     # the chips under the headline — "Fixed fee"
    "years",          # years in practice
    # The stars, the count and the attribution together: one claim, shown or
    # gone as one. Stars without the count that sizes them, or a count without
    # the source that legitimises it, would each be half a claim.
    "rating",
    "reviews",        # the written reviews under the stars, hideable on their own
    "specialties",    # what they do, as a list
    "education",      # where they trained
    # The fee table and the fee note beside it — one block, so the note can
    # never be left annotating prices the buyer cannot see.
    "costs",
    "faq",            # the question-and-answer block
    # The block of public owner-defined answers. Hiding it hides the block;
    # the per-field `public` flags still decide what would have gone in it.
    "details",
    "license",        # the licence number — on the record, off the page until switched on
    "vat_number",     # the VAT / ΑΦΜ, likewise
)

# Every key except the two columns that have never been published. Derived
# from FIELD_KEYS rather than written out, so the default cannot drift from
# the vocabulary: a key added later starts switched off, and a database where
# nobody has touched either column keeps publishing exactly what it published
# before any of this existed. `license` and `vat_number` are on the record;
# they reach a buyer only when somebody deliberately turns them on.
DEFAULT_VISIBLE: frozenset[str] = frozenset(FIELD_KEYS) - {"license", "vat_number"}

_KNOWN: frozenset[str] = frozenset(FIELD_KEYS)


def resolve(
    professional_visible: Sequence[str] | None,
    profession_visible: Sequence[str] | None,
) -> frozenset[str]:
    """The keys one record may publish.

    The professional's list when it has one, else its profession's, else the
    default. None means "inherit"; an empty list means "show nothing", which
    is a real, storable answer — folding it into None would turn the one
    decision that says "nothing" back into a default that says nearly
    everything, so empty is never treated as absent here.
    """
    if professional_visible is not None:
        return frozenset(professional_visible)
    if profession_visible is not None:
        return frozenset(profession_visible)
    return DEFAULT_VISIBLE


def validate(keys: Iterable[str]) -> list[str]:
    """The cleaned list: deduplicated, in FIELD_KEYS order.

    Raises Invalid naming the first unknown key rather than dropping it. A key
    outside the vocabulary names either a column that is never published (so
    there is nothing for it to turn on) or a mistake, and storing either would
    only be a way for junk to sit in the column looking load-bearing while
    deciding nothing.
    """
    for key in keys:
        if key not in _KNOWN:
            raise Invalid(f"Unknown field to publish: '{key}'")
    wanted = set(keys)
    return [key for key in FIELD_KEYS if key in wanted]

