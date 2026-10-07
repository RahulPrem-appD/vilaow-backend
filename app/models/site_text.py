"""Corrections to the website's text, made by hand in the admin.

The client's change requests of 5 October (item 10) put the site into English,
Greek and French, translated once and stored — and asked for "a simple way to
correct a translation by hand". The English and the stored translations live in
the website's code; a correction lives here, one row per string and language,
and the website lays it over both.

`key` is the string's address in the website's text catalog — `about.heroIntro`,
`msg.nav_how`, `guide.<slug>.body.3` — which the website owns. This table holds
no list of valid keys: a row whose key the site no longer has is simply never
read.

`source_hash` is the fingerprint of the English the correction was made
against (the website computes it). When the English changes afterwards, the
fingerprints no longer match and the admin flags the Greek or French as needing
a check. English corrections have none.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

#: The website's languages, in its order (src/lib/i18n/messages.ts).
SITE_LOCALES = ("en", "el", "fr")


class SiteText(Base):
    __tablename__ = "site_texts"
    __table_args__ = (UniqueConstraint("key", "locale", name="uq_site_texts_key_locale"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(300))
    locale: Mapped[str] = mapped_column(String(5))
    text: Mapped[str] = mapped_column(Text)
    source_hash: Mapped[str | None] = mapped_column(String(16))
    updated_by_id: Mapped[int | None] = mapped_column(ForeignKey("staff.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
