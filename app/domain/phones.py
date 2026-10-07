"""Turning a typed phone number into one WhatsApp can reach (change request 12).

WhatsApp addresses a person by their full international number, "+447700900123".
What the form and the admin hold is whatever somebody typed: "+44 7700 900123",
"0044 7700 900123", "691 234 5678", "(+30) 210-123 4567".

Two rules, because the two people are in different places:

  * a professional is in Greece, so a number typed without a country code is
    taken as Greek — and only a Greek mobile (69…) can be on WhatsApp;
  * a buyer is abroad, so a number without a country code could be anywhere.
    It is reported as unusable rather than guessed: a guess sends somebody's
    enquiry to a stranger.

Each answer carries the reason it failed, in words the admin shows next to the
step that could not be sent.
"""
from __future__ import annotations

import re
from typing import NamedTuple

_SEPARATORS = re.compile(r"[\s\-().\/]")
# "+44 (0) 7700 900123": the 0 is dialled only from inside the country, and
# kept it would make the number wrong — "+4407700…". Dropped before anything
# else, with its brackets.
_TRUNK_ZERO = re.compile(r"\(\s*0\s*\)")


class Reach(NamedTuple):
    number: str | None      # E.164, "+447700900123", or None
    problem: str | None     # why there is no number, for the admin


def _compact(raw: str) -> str:
    return _SEPARATORS.sub("", _TRUNK_ZERO.sub("", raw or ""))


def _international(compact: str) -> str | None:
    """"+447700900123" or "00447700900123" → "+447700900123"; otherwise None."""
    if compact.startswith("00"):
        compact = "+" + compact[2:]
    if not compact.startswith("+"):
        return None
    digits = compact[1:]
    # E.164 allows up to 15 digits with the country code, and no real number
    # is shorter than 8. A country code never starts with 0.
    if not digits.isdigit() or not 8 <= len(digits) <= 15 or digits.startswith("0"):
        return None
    return "+" + digits


def buyer_whatsapp(raw: str | None) -> Reach:
    if not (raw or "").strip():
        return Reach(None, "No phone number")
    number = _international(_compact(raw or ""))
    if number is None:
        return Reach(None, "The phone number has no country code, so it cannot be used on WhatsApp")
    return Reach(number, None)


def professional_whatsapp(raw: str | None) -> Reach:
    if not (raw or "").strip():
        return Reach(None, "No phone number on the professional's record")
    compact = _compact(raw or "")
    number = _international(compact)
    if number is None and compact.isdigit():
        if len(compact) == 10:                                   # 6912345678
            number = "+30" + compact
        elif len(compact) == 12 and compact.startswith("30"):    # 306912345678
            number = "+" + compact
    if number is None:
        return Reach(None, "The professional's phone number is not a full number")
    if number.startswith("+30") and not number.startswith("+3069"):
        return Reach(None, "The professional's number is a Greek landline, which cannot receive WhatsApp")
    return Reach(number, None)
