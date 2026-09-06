"""The Google summary is typed by a caller, and is not the review list.

Two different things end up as stars on a profile, and the whole point of
these tests is that they stay apart. The summary — a score and how many people
gave it — is what Google itself shows on the listing, copied onto the record
by whoever last looked. The review rows are individual reviews a caller typed
in, each with a name and words.

The summary used to arrive with the import and stay frozen. A listing moves,
so that kept the figure old rather than honest.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Professional, Review, Stage


def _published(db, professions, **kw):
    base = dict(
        business_name="Ktimatoemporiki", contact_name="Yiorgos Velvaskis",
        slug="yiorgos-velvaskis", city="Chania", region="Crete",
        profession_id=professions["agent"],
        published=True, stage=Stage.signed,
        published_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _profile(client, slug="yiorgos-velvaskis"):
    return client.get(f"/api/public/professionals/{slug}").json()


# ── a caller types the summary ───────────────────────────────────────────────
def test_a_caller_can_type_the_google_summary(as_caller, db, professions):
    p = _published(db, professions)

    r = as_caller.patch(f"/api/professionals/{p.id}",
                        json={"rating": 4.9, "review_count": 222})
    assert r.status_code == 200, r.text

    body = _profile(as_caller)
    assert body["rating"] == 4.9
    assert body["review_count"] == 222
    assert body["rating_source"] == "Google"


def test_a_caller_can_correct_a_figure_that_has_moved(as_caller, db, professions):
    """The reason the edit exists: a listing's score drifts and its count climbs."""
    p = _published(db, professions, rating=4.7, review_count=180)

    as_caller.patch(f"/api/professionals/{p.id}",
                    json={"rating": 4.9, "review_count": 222})
    body = _profile(as_caller)
    assert (body["rating"], body["review_count"]) == (4.9, 222)


def test_the_summary_is_the_only_thing_dated_nowhere(as_caller, db, professions):
    """No capture date travels with the figure. It was removed deliberately."""
    p = _published(db, professions, rating=4.9, review_count=222)
    assert "rating_captured_on" not in _profile(as_caller)
    listing = as_caller.get("/api/public/professionals").json()["items"][0]
    assert "rating_captured_on" not in listing


# ── the figure is bounded ────────────────────────────────────────────────────
def test_a_score_above_five_is_refused(as_caller, db, professions):
    p = _published(db, professions)
    assert as_caller.patch(f"/api/professionals/{p.id}",
                           json={"rating": 6}).status_code == 422


def test_a_negative_review_count_is_refused(as_caller, db, professions):
    p = _published(db, professions)
    assert as_caller.patch(f"/api/professionals/{p.id}",
                           json={"review_count": -1}).status_code == 422


def test_the_source_is_still_not_a_staff_edit(as_caller, db, professions):
    """It says which platform the number came from. Retyping a Google figure
    does not change that, so the field stays out of the update shape."""
    p = _published(db, professions, source="Google Maps")
    as_caller.patch(f"/api/professionals/{p.id}", json={"source": "Trustpilot"})
    db.refresh(p)
    assert p.source == "Google Maps"


# ── both, or neither ─────────────────────────────────────────────────────────
def test_a_score_with_no_count_publishes_nothing(as_caller, db, professions):
    """A score with nothing sizing it is half a claim."""
    p = _published(db, professions, rating=4.9)
    body = _profile(as_caller)
    assert body["rating"] is None
    assert body["review_count"] is None


def test_a_count_with_no_score_publishes_nothing(as_caller, db, professions):
    p = _published(db, professions, review_count=222)
    assert _profile(as_caller)["rating"] is None


def test_zero_reviews_is_not_a_summary(as_caller, db, professions):
    """"4.9 from 0 reviews" is not something anybody read off a listing."""
    p = _published(db, professions, rating=4.9, review_count=0)
    assert _profile(as_caller)["rating"] is None


def test_clearing_the_summary_returns_the_page_to_the_typed_reviews(
    as_caller, db, professions,
):
    p = _published(db, professions, rating=4.9, review_count=222)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4, source="via Google"))
    db.commit()
    assert _profile(as_caller)["review_count"] == 222

    as_caller.patch(f"/api/professionals/{p.id}",
                    json={"rating": None, "review_count": None})
    body = _profile(as_caller)
    assert body["rating"] == 4.0
    assert body["review_count"] == 1
    assert body["rating_source"] == "via Google"


# ── the two kinds of stars never mix ─────────────────────────────────────────
def test_the_summary_wins_without_touching_the_typed_reviews(as_caller, db, professions):
    p = _published(db, professions)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4,
                  text="Careful with the paperwork.", source="via Google"))
    db.commit()

    as_caller.patch(f"/api/professionals/{p.id}",
                    json={"rating": 4.9, "review_count": 222})

    body = _profile(as_caller)
    assert body["review_count"] == 222        # the summary, not the row count
    assert len(body["reviews"]) == 1          # the row is still there
    assert body["reviews"][0]["author"] == "Sarah M."


def test_without_a_summary_the_typed_reviews_are_what_publishes(
    as_caller, db, professions,
):
    """The fallback, unchanged: no summary means the rows speak for themselves."""
    p = _published(db, professions)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4, source="via Google"))
    db.commit()

    body = _profile(as_caller)
    assert body["rating"] == 4.0
    assert body["review_count"] == 1
    assert body["rating_source"] == "via Google"


def test_editing_the_summary_leaves_the_review_rows_alone(as_caller, db, professions):
    p = _published(db, professions)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4, source="via Google"))
    db.commit()
    before = db.query(Review).filter_by(professional_id=p.id).count()

    as_caller.patch(f"/api/professionals/{p.id}",
                    json={"rating": 4.9, "review_count": 222})
    assert db.query(Review).filter_by(professional_id=p.id).count() == before
