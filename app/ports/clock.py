"""Reading the time, as an interface.

Half the rules in this system are about time: a callback is due 24 hours after
it is made, a signing code expires after ten minutes, a review is requested a
few days after an introduction closes. Every one of those was previously
tested by writing a row, reaching into the database and back-dating it.

A clock that can be injected lets those rules be tested by advancing it, and —
more importantly — lets them be tested at all without a database.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    """The real one. Always timezone-aware UTC — a naive datetime on a signed
    agreement is a legal record with an ambiguous timestamp."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """For tests: starts at a fixed instant and only moves when told."""

    def __init__(self, at: datetime) -> None:
        if at.tzinfo is None:
            raise ValueError("FrozenClock needs an aware datetime")
        self._at = at

    def now(self) -> datetime:
        return self._at

    def advance(self, delta: timedelta) -> None:
        self._at += delta
