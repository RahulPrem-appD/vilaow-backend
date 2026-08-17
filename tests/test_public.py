"""What the public API must never do.

This is the only part of the system a stranger can read, so the tests are
written as leaks to prove impossible rather than features to prove present.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import Professional, Review, Stage


def _pro(db, professions, **kw):
    base = dict(
        business_name="Papadopoulos & Partners",
        contact_name="Kostas Papadopoulos",
        phone="+30 2810 555 999",           # internal: never public
        email="kostas@example.com",         # internal: never public
        notes="Sounded keen, call back Tuesday",   # internal
        city="Heraklion", region="Crete",
        profession_id=professions["lawyer"],
        rating=4.8, review_count=33, source="Google Maps",
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _published(db, professions, **kw):
    kw.setdefault("slug", "kostas-papadopoulos")   # overridable: some tests need two
    return _pro(db, professions, published=True,
                stage=Stage.signed, published_at=datetime.now(timezone.utc), **kw)


# ── visibility ──────────────────────────────────────────────────────────────
def test_unpublished_records_are_invisible(client, db, professions):
    _pro(db, professions, published=False, slug="not-live", stage=Stage.contacted)
    body = client.get("/api/public/professionals").json()
    assert body["total"] == 0
    assert body["items"] == []


def test_an_unpublished_slug_is_404_not_403(client, db, professions):
    """403 would confirm the record exists, letting anyone enumerate who is in
    the pipeline. It must be indistinguishable from a name we have never heard
    of."""
    _pro(db, professions, published=False, slug="secret-lawyer")
    assert client.get("/api/public/professionals/secret-lawyer").status_code == 404
    assert client.get("/api/public/professionals/no-such-person").status_code == 404


def test_published_records_are_visible_without_signing_in(client, db, professions):
    _published(db, professions)
    body = client.get("/api/public/professionals").json()
    assert body["total"] == 1
    assert body["items"][0]["name"] == "Kostas Papadopoulos"


# ── leakage ─────────────────────────────────────────────────────────────────
LEAKY = ["phone", "email", "notes", "stage", "assigned_to", "called_by",
         "batch_id", "vat_number", "password"]


@pytest.mark.parametrize("field", LEAKY)
def test_the_listing_never_carries_internal_fields(client, db, professions, field):
    _published(db, professions)
    assert field not in client.get("/api/public/professionals").text


@pytest.mark.parametrize("field", LEAKY)
def test_the_profile_never_carries_internal_fields(client, db, professions, field):
    _published(db, professions, vat_number="EL123456789", license="BAR-99")
    body = client.get("/api/public/professionals/kostas-papadopoulos").text
    assert field not in body


def test_the_direct_phone_number_is_never_published(client, db, professions):
    """A buyer asks for a callback; they do not get the professional's line.
    That is the whole product."""
    _published(db, professions)
    assert "2810 555 999" not in client.get("/api/public/professionals").text
    assert "2810 555 999" not in client.get("/api/public/professionals/kostas-papadopoulos").text


# ── attribution ─────────────────────────────────────────────────────────────
def test_a_rating_always_carries_its_source(client, db, professions):
    _published(db, professions)
    item = client.get("/api/public/professionals").json()["items"][0]
    assert item["rating"] == 4.8
    assert item["rating_source"] == "Google Maps"


def test_a_rating_with_no_source_is_withheld(client, db, professions):
    """Publishing another platform's number without saying whose it is would be
    wrong, so an unattributed rating is not shown at all."""
    _published(db, professions, source=None)
    item = client.get("/api/public/professionals").json()["items"][0]
    assert item["rating"] is None
    assert item["review_count"] is None


def test_reviews_come_back_with_their_provenance(client, db, professions):
    p = _published(db, professions)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=5,
                  text="Explained every step.", context="Completed purchase, May 2024",
                  source="via Google"))
    db.commit()
    r = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert r["reviews"][0]["source"] == "via Google"


# ── filters ─────────────────────────────────────────────────────────────────
def test_filtering_by_region(client, db, professions):
    _published(db, professions)
    _published(db, professions, slug="athens-person", city="Athens", region="Athens",
               contact_name="Someone Else")
    assert client.get("/api/public/professionals?region=Crete").json()["total"] == 1
    assert client.get("/api/public/professionals?region=Athens").json()["total"] == 1


def test_filtering_by_profession(client, db, professions):
    _published(db, professions)
    _published(db, professions, slug="an-agent", contact_name="Eleni Markaki",
               profession_id=professions["agent"])
    assert client.get("/api/public/professionals?role=lawyer").json()["total"] == 1
    assert client.get("/api/public/professionals?role=agent").json()["total"] == 1


def test_professions_are_public_so_the_chooser_can_render(client, professions):
    r = client.get("/api/public/professions")
    assert r.status_code == 200
    assert {p["key"] for p in r.json()} >= {"lawyer", "agent"}


def test_a_name_falls_back_to_the_firm(client, db, professions):
    """His sheet lists businesses. Until a caller captures who they spoke to,
    the firm's name is what there is."""
    _published(db, professions, contact_name=None)
    assert client.get("/api/public/professionals").json()["items"][0]["name"] == "Papadopoulos & Partners"


# ── the homepage numbers ────────────────────────────────────────────────────
def test_a_stat_with_nothing_behind_it_is_not_published(client, db):
    """His placeholder copy claims 300+ professionals and 2,400+ purchases
    against an empty database. A figure that cannot be substantiated must come
    back as nothing so the page can omit it, not as a flattering zero."""
    s = client.get("/api/public/stats").json()
    assert s["vetted_professionals"] is None
    assert s["purchases_supported"] is None
    assert s["average_rating"] is None
    assert s["rating_is_verified"] is False


def test_the_professional_count_is_the_real_one(client, db, professions):
    _published(db, professions)
    _published(db, professions, slug="second")
    _pro(db, professions, slug="unpublished-one", published=False)

    assert client.get("/api/public/stats").json()["vetted_professionals"] == 2


def test_only_vilaow_reviews_make_the_rating_verified(client, db, professions):
    """Google's numbers are not ours to call verified."""
    from app.models import Review, ReviewKind
    p = _published(db, professions)
    db.add(Review(professional_id=p.id, author="G.", stars=5, source="via Google",
                  kind=ReviewKind.google))
    db.commit()

    s = client.get("/api/public/stats").json()
    assert s["average_rating"] is None
    assert s["rating_is_verified"] is False

    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4,
                  source="Vilaow buyer", kind=ReviewKind.vilaow_verified))
    db.commit()

    s = client.get("/api/public/stats").json()
    assert s["average_rating"] == 4.0        # the Google 5 is excluded
    assert s["rating_is_verified"] is True
    assert s["rating_count"] == 1
