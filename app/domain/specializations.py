"""What a professional does, chosen from a list their trade decides.

The site had a free-text `specialties` column: whatever a caller typed, one
entry per line. It read badly on a profile — two long lines of similar prose
sitting side by side looked like the same thing printed twice — and it made
the one question a buyer actually has, "does this person do the thing I need",
impossible to answer by comparison. Two agents could describe the same service
in two different sentences and neither could be matched against the other.

So the trade owns a vocabulary and a limit, and a caller ticks from it during
the onboarding call. `professions.specializations` is the list, and
`professions.max_specializations` is how many of it one professional may show
— his numbers, and different per trade because an architect's four are worth
more than a tax advisor's ten.

The column the answers live in is still `professionals.specialties`. It is the
same fact about the same person, now answered from a list instead of a box,
and renaming it would have been churn for nothing.

A profession with no vocabulary keeps the old behaviour: anything the caller
types is accepted. That is not a transitional state to be tidied away later —
it is what lets the owner add a trade without having to invent its list in the
same breath.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.domain.errors import Invalid


def clean(
    chosen: Iterable[str] | None,
    allowed: list[str] | None,
    cap: int | None,
) -> list[str] | None:
    """The chosen services, in the vocabulary's own order.

    Ordering by the vocabulary rather than by what arrived means two
    professionals of the same trade list their services in the same sequence,
    so a buyer comparing two profiles is reading two of the same thing.

    Raises Invalid on a service the trade does not offer, or on more than the
    trade allows. Both are refused rather than trimmed: silently dropping the
    sixth tick would tell the caller they had saved something they had not.
    """
    if chosen is None:
        return None

    wanted = [entry.strip() for entry in chosen if entry and entry.strip()]
    # Deduplicated by first appearance. The admin's checkboxes cannot produce a
    # repeat, but the API is reachable without them.
    seen: set[str] = set()
    unique = [e for e in wanted if not (e in seen or seen.add(e))]

    if not allowed:
        # No vocabulary for this trade, so nothing to check against. The cap
        # still applies if the owner set one.
        if cap is not None and len(unique) > cap:
            raise Invalid(f"Choose at most {cap}.")
        return unique

    unknown = [e for e in unique if e not in allowed]
    if unknown:
        raise Invalid(f"Not one of this profession's services: '{unknown[0]}'")

    if cap is not None and len(unique) > cap:
        raise Invalid(f"Choose at most {cap} — {len(unique)} were sent.")

    return [entry for entry in allowed if entry in set(unique)]
