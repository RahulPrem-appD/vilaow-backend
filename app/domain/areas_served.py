"""Where a professional works, chosen from Vilaow's fixed area list.

Office location is already represented by ``city`` and ``region``. This list
is deliberately separate: it describes where the professional provides
services, which may span several areas and need not match their office.

Each group is named after the region it belongs to — spelled exactly as
``Professional.region`` stores it — so the directory can tell which region an
area is in. The island groups arrived with the client's change requests of 5
October, when the Aegean and Ionian islands opened.
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
    (
        "Aegean Islands",
        (
            "Paros",
            "Naxos",
            "Milos",
            "Rhodes",
        ),
    ),
    (
        "Ionian Islands",
        (
            "Lefkada",
            "Corfu",
            "Kefalonia",
            "Zakynthos",
        ),
    ),
)

AREA_OPTIONS: tuple[str, ...] = tuple(
    area for _, areas in AREA_GROUPS for area in areas
)
_KNOWN = frozenset(AREA_OPTIONS)


def is_area(value: str) -> bool:
    return value in _KNOWN


def areas_in(region: str) -> tuple[str, ...]:
    """The areas of one region; none for a region with no list."""
    return next((areas for name, areas in AREA_GROUPS if name == region), ())


def town_of(area: str) -> str:
    """The town an area is named after, for matching an office city.

    "Agios Nikolaos & Elounda" is Agios Nikolaos and "Sitia & East Crete" is
    Sitia; an area with one name is its own town.
    """
    return area.split(" & ")[0]


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
