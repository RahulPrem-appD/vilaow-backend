"""The two pieces that must not be wrong: who gets in, and what the sheet says.

These do not need the API, so they run even while the routers are in flux.
"""
from __future__ import annotations

import io
import os
import pathlib
from datetime import datetime, timezone

import pytest
from openpyxl import Workbook
from sqlalchemy import select

from app.importer import CATEGORY_TO_KEY, normalise_phone, parse_workbook
from app.models import Professional, Role, Staff
from app.security import authenticate, hash_password, verify_password

def _find_contact_sheet() -> pathlib.Path | None:
    """Where his actual spreadsheet lives on this machine.

    This used to be hardcoded to /tmp/vilaow-contacts.xlsx, which /tmp clears —
    so these four tests had been skipping silently for a long time, and a skip
    reads as a pass in the summary. They guard the importer against ingesting
    band rows as people, which is the bug that once cost 216 Cretan rows their
    region, so having them quietly not run was worse than not having them.

    The sheet is not committed: it holds 322 real names and phone numbers, and
    client contact data does not belong in a repository. So it is looked up
    where it actually tends to be, and VILAOW_CONTACT_SHEET overrides for CI.
    """
    override = os.environ.get("VILAOW_CONTACT_SHEET")
    candidates = [pathlib.Path(override)] if override else []
    candidates += [
        pathlib.Path.home() / "Downloads" / "Vilaow-Contacts (1).xlsx",
        pathlib.Path.home() / "Downloads" / "Vilaow-Contacts.xlsx",
        pathlib.Path("/tmp/vilaow-contacts.xlsx"),
    ]
    return next((c for c in candidates if c.exists()), None)


REAL_SHEET = _find_contact_sheet()


# ── passwords ───────────────────────────────────────────────────────────────
def test_hash_is_not_the_password():
    h = hash_password("hunter2")
    assert "hunter2" not in h
    assert h.startswith("$2b$")


def test_verify_round_trip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h)
    assert not verify_password("Correct horse", h)


def test_same_password_hashes_differently():
    """Distinct salts, so two staff with the same password are not obvious
    from the table."""
    assert hash_password("same") != hash_password("same")


def test_long_password_is_not_truncated_at_72_bytes():
    """bcrypt reads 72 bytes and ignores the rest. Without pre-hashing, these
    two would be the same password."""
    a = "x" * 100
    b = "x" * 72 + "completely different tail"
    assert not verify_password(b, hash_password(a))


def test_malformed_hash_returns_false_rather_than_raising():
    assert verify_password("anything", "not-a-bcrypt-hash") is False


# ── authentication ──────────────────────────────────────────────────────────
def test_authenticate_accepts_correct_credentials(db):
    db.add(Staff(name="A", email="a@x.com", password_hash=hash_password("pw"), role=Role.caller))
    db.commit()
    assert authenticate(db, "a@x.com", "pw") is not None


def test_authenticate_rejects_wrong_password(db):
    db.add(Staff(name="A", email="a@x.com", password_hash=hash_password("pw"), role=Role.caller))
    db.commit()
    assert authenticate(db, "a@x.com", "nope") is None


def test_authenticate_rejects_inactive_staff_with_the_right_password(db):
    """Deactivating someone must lock them out, not merely hide them."""
    db.add(Staff(name="Gone", email="gone@x.com",
                 password_hash=hash_password("pw"), role=Role.caller, active=False))
    db.commit()
    assert authenticate(db, "gone@x.com", "pw") is None


def test_authenticate_is_case_insensitive_on_email(db):
    db.add(Staff(name="A", email="a@x.com", password_hash=hash_password("pw"), role=Role.caller))
    db.commit()
    assert authenticate(db, "  A@X.COM ", "pw") is not None


def test_unknown_email_returns_none(db):
    assert authenticate(db, "nobody@x.com", "pw") is None


# ── phone normalisation ─────────────────────────────────────────────────────
@pytest.mark.parametrize("a,b", [
    ("+30 697 044 7994", "6970447994"),
    ("0030 697 044 7994", "+306970447994"),
    ("+30 2821 405070", "2821405070"),
])
def test_same_number_written_differently_normalises_the_same(a, b):
    assert normalise_phone(a) == normalise_phone(b)


def test_different_numbers_stay_different():
    assert normalise_phone("+30 697 044 7994") != normalise_phone("+30 697 044 7995")


# ── his actual spreadsheet ──────────────────────────────────────────────────
needs_sheet = pytest.mark.skipif(
    REAL_SHEET is None,
    reason="his contact sheet is not on this machine — set VILAOW_CONTACT_SHEET to run these",
)


@needs_sheet
def test_parses_his_sheet_without_ingesting_furniture():
    """The failure this guards against: 11 band rows and 6 repeated headers
    imported as people called things like 'Chania  (67 contacts)'."""
    r = parse_workbook(REAL_SHEET)
    assert r.seen == 327
    assert r.accepted == 323
    assert r.skipped_no_phone == 4
    assert r.band_rows == 11
    assert r.header_rows == 6
    assert not [x for x in r.rows if "contacts)" in x.business_name]
    assert not [x for x in r.rows if x.business_name.lower() == "category"]


@needs_sheet
def test_every_row_gets_a_region_and_a_city():
    """There is no city column in his sheet — the city is the band you are
    standing in. An earlier parser lost the region on all 216 Cretan rows
    because 'CRETE (Chania · ...)' contains a bracket."""
    r = parse_workbook(REAL_SHEET)
    assert [x for x in r.rows if not x.region] == []
    assert [x for x in r.rows if not x.city] == []


@needs_sheet
def test_regions_and_their_cities_are_right():
    r = parse_workbook(REAL_SHEET)
    by_region: dict[str, set[str]] = {}
    for x in r.rows:
        by_region.setdefault(x.region, set()).add(x.city)
    assert by_region["Crete"] == {"Chania", "Heraklion", "Rethymno", "Agios Nikolaos"}
    assert by_region["Athens"] == {"Athens"}
    assert by_region["Thessaloniki"] == {"Thessaloniki"}


@needs_sheet
def test_every_category_maps_to_a_known_profession():
    """His sheet says "Accountant" where we say "tax accountant". If he adds a
    category we do not know, this fails rather than silently dropping rows."""
    r = parse_workbook(REAL_SHEET)
    assert r.skipped_unknown_category == 0
    assert {x.profession_key for x in r.rows} <= set(CATEGORY_TO_KEY.values())


# ── his Google Maps list (1 November 2026) ──────────────────────────────────
MAP = "https://www.google.com/maps/place/?q=place_id:{}"


def _google_maps_list() -> io.BytesIO:
    """His Google Maps list in miniature: a Summary sheet of counts first, then
    the table, then the same rows again in a sheet per destination."""
    wb = Workbook()
    summary = wb.active
    summary.title = "Summary"
    summary.append(["Vilaow – Professionals by Destination & Sector (Google Maps)"])
    summary.append(["Destination", "Lawyer", "Engineer", "Total"])
    summary.append(["Athens & Athens Riviera", 1, 1, 2])

    header = ["Destination", "Area", "Sector", "Name", "Phone", "Address",
              "Rating", "Reviews", "Google Maps", "Note"]
    rows = [
        ["Athens & Athens Riviera", "Athens", "Lawyer", "Law Office A",
         "+30 210 000 0001", "Street 1", 4.8, 120, "place-a", None],
        ["Thessaloniki & Halkidiki", "Halkidiki", "Engineer", "Engineer B",
         "+30 2310 000002", "Street 2", None, None, "place-b", "Mixed reviews - check"],
        ["Agios Nikolaos", "Agios Nikolaos", "Property Manager", "Manager C",
         "6900000003", "Street 3", 5, 9, "place-c", None],
        ["Corfu", "Corfu", "Tax Advisor", "Advisor D",
         None, "Street 4", 4.1, 30, "place-d", None],
        ["Paros", "Paros", "Notary", "Notary E",
         "6900000005", "Street 5", 4.5, 12, "place-e", None],
    ]
    for title, sheet_rows in (("All Professionals", rows), ("Athens & Riviera", rows[:1])):
        ws = wb.create_sheet(title)
        ws.append(header)
        for row in sheet_rows:
            ws.append([*row[:8], "Open map", row[9]])
            ws.cell(row=ws.max_row, column=9).hyperlink = MAP.format(row[8])

    out = io.BytesIO()
    wb.save(out)
    out.seek(0)
    return out


def test_reads_the_table_not_the_summary_in_front_of_it():
    """Reading the first sheet read his 15 rows of counts, and skipped them
    all. The rows repeated in the per-destination sheets are not read twice."""
    r = parse_workbook(_google_maps_list())
    assert (r.seen, r.accepted, r.skipped_no_phone, r.skipped_unknown_category) == (5, 3, 1, 1)
    assert [(x.profession_key, x.region, x.city) for x in r.rows] == [
        ("lawyer", "Athens", "Athens"),
        ("engineer", "Thessaloniki", "Halkidiki"),
        ("property_manager", "Crete", "Agios Nikolaos"),
    ]
    first = r.rows[0]
    assert (first.business_name, first.phone, first.address) == ("Law Office A", "+30 210 000 0001", "Street 1")
    assert (first.rating, first.review_count) == (4.8, 120)
    assert first.maps_url == MAP.format("place-a")
    assert r.rows[1].notes == "Mixed reviews - check"


def test_the_import_keeps_the_map_link(as_owner, professions, db):
    r = as_owner.post(
        "/api/imports",
        files={"file": ("google-maps.xlsx", _google_maps_list(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert r.status_code == 201, r.text
    assert r.json()["batch"]["rows_added"] == 3
    pro = db.scalar(select(Professional).where(Professional.business_name == "Engineer B"))
    assert (pro.region, pro.city) == ("Thessaloniki", "Halkidiki")
    assert pro.notes == f"Mixed reviews - check | Google Maps: {MAP.format('place-b')}"


def _find_google_maps_list() -> pathlib.Path | None:
    """His full list, where it tends to be. Not committed, for the same reason
    as his first sheet; VILAOW_GOOGLE_MAPS_LIST overrides."""
    override = os.environ.get("VILAOW_GOOGLE_MAPS_LIST")
    candidates = [pathlib.Path(override)] if override else []
    candidates += [
        pathlib.Path.home() / "Downloads" / name
        for name in ("vilaow-professionals-google-maps-EN_1.xlsx",
                     "vilaow-professionals-google-maps-EN.xlsx")
    ]
    return next((c for c in candidates if c.exists()), None)


GOOGLE_MAPS_LIST = _find_google_maps_list()


@pytest.mark.skipif(
    GOOGLE_MAPS_LIST is None,
    reason="his Google Maps list is not on this machine — set VILAOW_GOOGLE_MAPS_LIST to run this",
)
def test_parses_his_whole_google_maps_list():
    r = parse_workbook(GOOGLE_MAPS_LIST)
    assert (r.seen, r.accepted, r.skipped_no_phone, r.skipped_unknown_category) == (1956, 1948, 8, 0)
    assert [x for x in r.rows if not x.region or not x.city or not x.maps_url] == []


def test_a_batch_pages_through_in_order_without_repeats(as_owner, professions, db):
    """An import adds every row in one transaction, so all its rows share one
    created_at: 1,922 of them on 2 November. Sorted on that alone, the
    worklist's pages could repeat some rows and miss others."""
    same = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    db.add_all(
        Professional(business_name=f"Row {i:02d}", phone=f"6900000{i:03d}",
                     profession_id=professions["lawyer"], created_at=same)
        for i in range(30)
    )
    db.commit()

    names = []
    for offset in range(0, 30, 7):
        r = as_owner.get(f"/api/professionals?limit=7&offset={offset}")
        assert r.status_code == 200, r.text
        names += [p["business_name"] for p in r.json()["items"]]
    assert names == [f"Row {i:02d}" for i in range(30)]
