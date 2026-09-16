"""Where a professional works, chosen from Vilaow's fixed area list.

Office location is already represented by ``city`` and ``region``. This list
is deliberately separate: it describes where the professional provides
services, which may span several areas and need not match their office.
"""
from __future__ import annotations

from collections.abc import Iterable

from app.domain.errors import Invalid


AREA_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Athens",
        (
            "Central Athens",
            "North Athens",
            "Athens Riviera",
            "East Attica",
            "Piraeus",
        ),
    ),
    (
        "Thessaloniki",
        (
            "Thessaloniki",
            "Halkidiki",
        ),
    ),
    (
        "Crete",
        (
            "Chania",
            "Rethymno",
            "Heraklion",
            "Agios Nikolaos & Elounda",
            "Sitia & East Crete",
        ),
    ),
)

AREA_OPTIONS: tuple[str, ...] = tuple(
    area for _, areas in AREA_GROUPS for area in areas
)
_KNOWN = frozenset(AREA_OPTIONS)


def clean(chosen: Iterable[str] | None) -> list[str] | None:
    """Validate, deduplicate, and return selections in display order."""
    if chosen is None:
        return None

    values = [area.strip() for area in chosen if area and area.strip()]
    unknown = next((area for area in values if area not in _KNOWN), None)
    if unknown is not None:
        raise Invalid(f"Unknown area served: '{unknown}'")

    selected = set(values)
    return [area for area in AREA_OPTIONS if area in selected]
