"""The two pieces that must not be wrong: who gets in, and what the sheet says.

These do not need the API, so they run even while the routers are in flux.
"""
from __future__ import annotations

import os
import pathlib

import pytest

from app.importer import CATEGORY_TO_KEY, normalise_phone, parse_workbook
from app.models import Role, Staff
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
