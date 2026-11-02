"""Read the contact spreadsheets the client actually works from.

His first sheet is not a flat table. It is grouped for reading:

    Vilaow — Professional Outreach List          <- title
    Real, publicly listed businesses ...         <- description
    CRETE (Chania · Heraklion · ...)             <- region band
    Chania  (67 contacts)                        <- city band
    Category | Business / Name | Phone | ...     <- header, REPEATED per city
    Estate Agent | ARENCORES ... | +30 ...       <- data
    ...
    Heraklion  (65 contacts)                     <- next city band
    Category | ...                               <- header again

A parser that assumes "row 1 is the header, the rest is data" ingests the
11 band rows and 6 repeated headers as if they were 17 people called things
like "Chania  (67 contacts)". It also loses the city entirely, because there
is no city column — the city is the band you are standing in.

His Google Maps list (1 November 2026) is flat, with the place in columns of
its own and a link to each business on Google Maps:

    Destination | Area | Sector | Name | Phone | Address | Rating | Reviews | Google Maps | Note

It opens with a Summary sheet of counts, then lists every row twice: in "All
Professionals" and again in a sheet per destination. Reading the first sheet,
as this once did, read the 15 rows of counts and skipped them all.

So this reads one sheet: the one named Contacts, or else the first with a
header row. It reads each column by its header, keeps track of the current
region and city for the grouped layout, and only accepts a row whose category
is a known profession.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from openpyxl import load_workbook

from app.domain.areas_served import AREA_GROUPS

# The seven he uses, plus the spellings his sheets actually contain. His first
# sheet says "Estate Agent" and "Accountant"; his Google Maps list says "Real
# Estate Agent", "Engineer" and "Tax Advisor".
CATEGORY_TO_KEY = {
    "estate agent": "agent",
    "real estate agent": "agent",
    "lawyer": "lawyer",
    "civil engineer": "engineer",
    "engineer": "engineer",
    "architect": "architect",
    "contractor": "contractor",
    "property manager": "property_manager",
    "accountant": "accountant",
    "tax accountant": "accountant",
    "tax advisor": "accountant",
}

# Header text -> the field its column holds, in both of his layouts.
COLUMNS = {
    "category": "category",
    "sector": "category",
    "business / name": "name",
    "name": "name",
    "phone": "phone",
    "address": "address",
    "rating": "rating",
    "reviews": "reviews",
    "outreach status": "status",
    "status": "status",
    "notes": "notes",
    "note": "notes",
    "google maps": "maps",
    "destination": "destination",
    "area": "area",
}

# Before any header row, his first sheet's column order.
FIRST_SHEET_COLUMNS = {
    "category": 0, "name": 1, "phone": 2, "address": 3,
    "rating": 4, "reviews": 5, "status": 6, "notes": 7,
}


def _place_key(name: str) -> str:
    # "Athens & Athens Riviera" -> "athens", "Agios Nikolaos & Elounda" ->
    # "agios nikolaos": his places and ours differ only after the "&".
    return name.split("&")[0].strip().lower()


# Region, or area, -> the region as Professional.region spells it.
REGION_OF = {_place_key(region): region for region, _ in AREA_GROUPS}
for _region, _areas in AREA_GROUPS:
    for _area in _areas:
        REGION_OF.setdefault(_place_key(_area), _region)

# A region band is written in capitals; a city band is title case with a count.
REGION_BAND = re.compile(r"^[A-Z][A-Z\s·]+$")


@dataclass
class Row:
    profession_key: str
    business_name: str
    phone: str | None
    address: str | None
    city: str | None
    region: str | None
    rating: float | None
    review_count: int | None
    status: str | None
    notes: str | None
    maps_url: str | None = None


@dataclass
class ImportResult:
    rows: list[Row] = field(default_factory=list)
    seen: int = 0
    skipped_no_phone: int = 0
    skipped_unknown_category: int = 0
    band_rows: int = 0
    header_rows: int = 0

    @property
    def accepted(self) -> int:
        return len(self.rows)


def _text(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def _number(v, cast):
    try:
        return cast(str(v).strip())
    except (TypeError, ValueError):
        return None


def _header(values) -> dict[str, int] | None:
    """The column of each field a header row names; None for any other row."""
    columns: dict[str, int] = {}
    for i, value in enumerate(values):
        name = COLUMNS.get(" ".join((_text(value) or "").lower().split()))
        if name and name not in columns:
            columns[name] = i
    return columns if {"category", "phone"} <= columns.keys() else None


def _link(cell) -> str | None:
    """Where a cell points: his "Open map" cells are hyperlinks."""
    hyperlink = getattr(cell, "hyperlink", None)
    if hyperlink is not None and hyperlink.target:
        return hyperlink.target
    text = _text(cell.value)
    return text if text and text.startswith(("https://", "http://")) else None


def _region_of(*places: str | None) -> str | None:
    for place in places:
        if place and _place_key(place) in REGION_OF:
            return REGION_OF[_place_key(place)]
    return None


def _pick_sheet(wb, sheet_name: str):
    if sheet_name in wb.sheetnames:
        return wb[sheet_name]
    for ws in wb.worksheets:
        if any(_header(values) for values in ws.iter_rows(values_only=True)):
            return ws
    return wb.worksheets[0]


def parse_workbook(path_or_stream, sheet_name: str = "Contacts") -> ImportResult:
    # Not read-only: a read-only workbook has no hyperlinks.
    wb = load_workbook(path_or_stream, data_only=True)
    ws = _pick_sheet(wb, sheet_name)

    out = ImportResult()
    columns = FIRST_SHEET_COLUMNS
    region: str | None = None
    city: str | None = None

    for cells in ws.iter_rows():
        values = [cell.value for cell in cells] + [None] * (8 - len(cells))
        first = _text(values[0])
        if not first:
            continue

        # The header: once per city block in his first sheet, once in his
        # Google Maps list.
        header = _header(values)
        if header:
            columns = header
            out.header_rows += 1
            continue

        # A band row: only the first cell carries anything. Three kinds, and
        # telling them apart matters — an earlier version treated anything
        # non-capitalised as a city, which made the sheet's title row a city
        # and, because "CRETE (Chania · ...)" contains a bracket, left every
        # Cretan row with no region at all: 216 of 323.
        if all(_text(c) is None for c in values[1:]):
            out.band_rows += 1
            head = first.split("(")[0].strip()
            if head.isupper():
                region = head.title()                    # CRETE -> Crete
            elif "contact" in first.lower() and "(" in first:
                city = head or None                      # Chania  (67 contacts)
            # anything else is the sheet's title or description — ignore it
            continue

        def get(name: str):
            i = columns.get(name)
            return values[i] if i is not None else None

        out.seen += 1
        key = CATEGORY_TO_KEY.get((_text(get("category")) or "").lower())
        if key is None:
            out.skipped_unknown_category += 1
            continue

        phone = _text(get("phone"))
        if not phone:
            # He notes these himself: Google does not list a number publicly
            # for some businesses. Without one there is nothing to call.
            out.skipped_no_phone += 1
            continue

        row_region, row_city = region, city
        if "destination" in columns or "area" in columns:
            destination, area = _text(get("destination")), _text(get("area"))
            row_region, row_city = _region_of(destination, area), area or destination

        maps = columns.get("maps")
        out.rows.append(
            Row(
                profession_key=key,
                business_name=_text(get("name")) or "(no name)",
                phone=phone,
                address=_text(get("address")),
                city=row_city,
                region=row_region,
                rating=_number(get("rating"), float),
                review_count=_number(get("reviews"), int),
                status=_text(get("status")),
                notes=_text(get("notes")),
                maps_url=_link(cells[maps]) if maps is not None else None,
            )
        )

    wb.close()
    return out


def normalise_phone(phone: str) -> str:
    """For duplicate detection only — the original is what gets dialled.

    His sheet mixes "+30 697 044 7994" and "6947713390"; the same business
    imported twice must not become two rows to ring.
    """
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("0030"):
        digits = digits[4:]
    elif digits.startswith("30") and len(digits) > 10:
        digits = digits[2:]
    return digits
