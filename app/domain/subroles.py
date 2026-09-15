"""The one-line title under a professional's name, chosen from a list.

`professionals.subrole` was a free-text box: "Real estate Lawyer", "Luxury
real estate agent", "Property Lawyer · Foreign Buyer Transactions" — whatever a
caller wrote. Three lawyers could describe the same job three ways, and the
line was never something a buyer could compare across two profiles.

So the trade owns the list — `professions.subroles`, his words per trade — and
a caller picks one on the call. One, not several: it is a title, the bold line
under the name on the card, and a title with three parts is a sentence.

A trade with no list keeps the box it always had. That is the state a newly
added profession starts in, not a gap to be closed.
"""
from __future__ import annotations

from app.domain.errors import Invalid


def clean(chosen: str | None, allowed: list[str] | None) -> str | None:
    """The chosen sub-role, or None for none.

    Raises Invalid on a title the trade does not list, rather than storing it:
    the record editor offers only the list, so anything else arriving is
    either a stale value from before the trade had a list or a request made
    around the form — and in both cases silently keeping it would put a line
    on a public profile that nobody chose from anything.
    """
    if chosen is None:
        return None
    title = chosen.strip()
    if not title:
        return None
    if allowed and title not in allowed:
        raise Invalid(f"Not one of this profession's sub-roles: '{title}'")
    return title
