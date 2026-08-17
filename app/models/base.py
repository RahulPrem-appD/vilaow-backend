"""The declarative base, and the one helper every table shares.

Kept in its own module so each aggregate can import it without importing its
siblings — which is what stops the split from turning into a circular graph.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
