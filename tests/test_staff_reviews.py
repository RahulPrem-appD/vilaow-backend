"""Google reviews entered by staff, and the rating the rows add up to.

A signed-in caller can type in a rating copied from a professional's public
listing, and where a record has no Google summary of its own the public
rating is computed from those rows: their average, their count, their source.

That fallback is what this file is about. The summary columns take priority
over it — see test_google_rating_edit.py — so the fixtures here deliberately
leave them empty. A test about what the rows add up to should not also be
carrying a number that would win.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Event, Professional, Review, ReviewKind, Stage


def _pro(db, professions, **kw):
    # No rating or review_count here. A record carrying a Google summary
    # publishes it instead of averaging its rows, so a fixture that set one
    # would be testing the other path in every test below. `source` stays: it
    # is where the scraped figure came from and it publishes nothing by itself.
    base = dict(
        business_name="Papadopoulos & Partners",
        contact_name="Kostas Papadopoulos",
        city="Heraklion", region="Crete",
        profession_id=professions["lawyer"],
        source="Google Maps",
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _published(db, professions, **kw):
    kw.setdefault("slug", "kostas-papadopoulos")
    return _pro(db, professions, published=True,
                stage=Stage.signed, published_at=datetime.now(timezone.utc), **kw)


def _google(db, p, stars, **kw):
    kw.setdefault("author", "G.")
    kw.setdefault("source", "via Google")
    r = Review(professional_id=p.id, stars=stars, kind=ReviewKind.google, **kw)
    db.add(r)
    db.commit()
    return r


def _events(db, professional_id):
    return {e.kind for e in
            db.query(Event).filter_by(professional_id=professional_id).all()}


# ── entering a Google review ────────────────────────────────────────────────
def test_a_staff_member_can_enter_a_google_review(as_caller, db, professions):
    p = _published(db, professions)
    r = as_caller.post(f"/api/professionals/{p.id}/reviews", json={
        "author": "Maria P.",
        "stars": 5,
        "text": "Handled everything while we were abroad.",
        "context": "Pre-purchase survey, May 2024",
    })
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "google"
    assert body["source"] == "via Google"

    row = db.query(Review).filter_by(id=body["id"]).one()
    assert row.kind is ReviewKind.google
    assert row.introduction_id is None
    assert row.stars == 5
    assert row.context == "Pre-purchase survey, May 2024"

    assert "google_review_added" in _events(db, p.id)


def test_an_entered_review_is_what_the_public_profile_shows(as_caller, client, db, professions):
    p = _published(db, professions)
    assert as_caller.post(f"/api/professionals/{p.id}/reviews",
                          json={"author": "Maria P.", "stars": 4}).status_code == 201

    profile = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert profile["rating"] == 4.0
    assert profile["review_count"] == 1
    assert profile["rating_source"] == "via Google"


def test_two_google_reviews_average_to_one_decimal(client, db, professions):
    p = _published(db, professions)
    _google(db, p, 4)
    _google(db, p, 5)

    profile = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert profile["rating"] == 4.5
    assert profile["review_count"] == 2


# ── a record with no summary of its own ─────────────────────────────────────
def test_a_record_with_neither_summary_nor_rows_publishes_no_rating(
    client, db, professions,
):
    """Nothing to average and nothing typed, so the page prints no stars at
    all rather than an empty rating."""
    _published(db, professions)

    profile = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert profile["rating"] is None
    assert profile["review_count"] is None
    assert profile["rating_source"] is None

    listing = client.get("/api/public/professionals").json()["items"][0]
    assert listing["rating"] is None
    assert listing["review_count"] is None
    assert listing["rating_source"] is None


def test_a_summary_publishes_over_the_rows_beneath_it(client, db, professions):
    """The priority, stated here as well as in test_google_rating_edit.py,
    because this is the file whose fixtures depend on knowing it."""
    p = _published(db, professions, rating=4.8, review_count=33)
    _google(db, p, 4)

    profile = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert profile["rating"] == 4.8
    assert profile["review_count"] == 33
    assert profile["rating_source"] == "Google"
    assert len(profile["reviews"]) == 1


def test_the_admin_sees_the_summary_columns(as_caller, client, db, professions):
    p = _published(db, professions, rating=4.8, review_count=33)
    r = as_caller.get(f"/api/professionals/{p.id}")
    assert r.status_code == 200, r.text
    assert r.json()["rating"] == 4.8
    assert r.json()["review_count"] == 33
    assert r.json()["source"] == "Google Maps"


# ── attribution ─────────────────────────────────────────────────────────────
def test_google_and_verified_rows_are_attributed_to_both(client, db, professions):
    p = _published(db, professions)
    _google(db, p, 5)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4,
                  kind=ReviewKind.vilaow_verified, source="Vilaow buyer"))
    db.commit()

    profile = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert profile["rating"] == 4.5
    assert profile["review_count"] == 2
    assert profile["rating_source"] == "Google and Vilaow buyers"


def test_the_listing_agrees_with_the_profile(client, db, professions):
    p = _published(db, professions)
    _google(db, p, 5)
    _google(db, p, 4)

    item = client.get("/api/public/professionals").json()["items"][0]
    assert item["rating"] == 4.5
    assert item["review_count"] == 2
    assert item["rating_source"] == "via Google"


# ── the listing order follows the computed average ──────────────────────────
def test_the_directory_orders_by_the_average_the_buyer_sees(client, db, professions):
    """Not by the imported column. Here the scraped ratings point the other
    way, and the sort must ignore them."""
    high = _published(db, professions, slug="higher", contact_name="Higher Rated",
                      rating=3.0, review_count=2, source="Google Maps")
    _google(db, high, 5)
    _google(db, high, 4)

    low = _published(db, professions, slug="lower", contact_name="Lower Rated",
                     rating=5.0, review_count=88, source="Google Maps")
    _google(db, low, 4)
    _google(db, low, 3)

    items = client.get("/api/public/professionals").json()["items"]
    assert [i["name"] for i in items[:2]] == ["Higher Rated", "Lower Rated"]


def test_professionals_with_no_reviews_still_list(client, db, professions):
    """A LEFT JOIN, not a filter: no rows behind a card must not drop the
    card, only its rating."""
    _published(db, professions, contact_name="No Reviews Yet")
    listing = client.get("/api/public/professionals").json()
    assert listing["total"] == 1
    assert listing["items"][0]["name"] == "No Reviews Yet"
    assert listing["items"][0]["rating"] is None


# ── removal ─────────────────────────────────────────────────────────────────
def test_a_typed_review_can_be_deleted_and_the_count_drops(as_caller, client, db, professions):
    p = _published(db, professions)
    first = as_caller.post(f"/api/professionals/{p.id}/reviews",
                           json={"author": "Maria P.", "stars": 5}).json()
    as_caller.post(f"/api/professionals/{p.id}/reviews",
                   json={"author": "Nikos K.", "stars": 4})

    r = as_caller.delete(f"/api/professionals/{p.id}/reviews/{first['id']}")
    assert r.status_code == 204, r.text

    profile = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert profile["review_count"] == 1
    assert profile["rating"] == 4.0
    assert "google_review_deleted" in _events(db, p.id)


def test_a_review_belonging_to_another_professional_is_not_found(as_caller, db, professions):
    mine = _published(db, professions)
    other = _published(db, professions, slug="someone-else", contact_name="Someone Else")
    review = _google(db, other, 5)

    r = as_caller.delete(f"/api/professionals/{mine.id}/reviews/{review.id}")
    assert r.status_code == 404
    assert db.query(Review).filter_by(id=review.id).count() == 1


def test_a_buyers_review_survives_a_delete_request(as_caller, client, db, professions):
    """Clause 4 promises reviews "cannot be bought, edited or removed on
    request". That is only true if the delete endpoint makes it true."""
    p = _published(db, professions)
    review = Review(professional_id=p.id, author="Sarah M.", stars=5,
                    kind=ReviewKind.vilaow_verified, source="Vilaow buyer")
    db.add(review)
    db.commit()

    r = as_caller.delete(f"/api/professionals/{p.id}/reviews/{review.id}")
    assert r.status_code == 409
    assert db.query(Review).filter_by(id=review.id).count() == 1

    profile = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert profile["review_count"] == 1


# ── the door is locked ──────────────────────────────────────────────────────
def test_entering_a_review_needs_a_login(client, db, professions):
    p = _published(db, professions)
    r = client.post(f"/api/professionals/{p.id}/reviews",
                    json={"author": "Maria P.", "stars": 5})
    assert r.status_code == 401


def test_deleting_a_review_needs_a_login(client, db, professions):
    p = _published(db, professions)
    review = _google(db, p, 5)
    assert client.delete(f"/api/professionals/{p.id}/reviews/{review.id}").status_code == 401
