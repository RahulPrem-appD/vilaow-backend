"""Corrections to the website's text — request and response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel

SiteLocale = Literal["en", "el", "fr"]

#: A catalog key: dot-separated path segments. Generous, because a term's key
#: is its English ("term.Planning & Licensing (Πολεοδομία)"), but never a
#: control character, and never long.
_KEY_MAX = 300
#: Longer than any one string on the site — a guide's paragraph runs to about
#: 1,500 characters — with room to spare.
_TEXT_MAX = 20_000


# ── one correction ───────────────────────────────────────────────────────────
class SiteTextOut(ORMModel):
    key: str
    locale: SiteLocale
    text: str
    source_hash: str | None
    updated_at: datetime
    #: Who made it, for the admin's "last changed by" line.
    updated_by: str | None = None


class SiteTextIn(BaseModel):
    key: str = Field(min_length=1, max_length=_KEY_MAX)
    locale: SiteLocale
    text: str = Field(max_length=_TEXT_MAX)
    #: The fingerprint of the English this was written against. Required for
    #: Greek and French, so the admin can tell later whether the English moved.
    source_hash: str | None = Field(default=None, max_length=16)

    @field_validator("key")
    @classmethod
    def _printable(cls, key: str) -> str:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in key) or key != key.strip():
            raise ValueError("not a text key")
        return key


# ── what the website reads ───────────────────────────────────────────────────
class PublicSiteTexts(BaseModel):
    """One language's corrections: key → text."""
    locale: SiteLocale
    texts: dict[str, str]
