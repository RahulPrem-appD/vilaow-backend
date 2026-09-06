"""The Google summary is typed by a caller, and is not the review list.

Two different things end up as stars on a profile, and the whole point of
these tests is that they stay apart. The summary — a score, a count and the
day somebody read it — is what Google itself shows on the listing. The review
rows are individual reviews a caller typed in, each with a name and words.

The summary used to arrive with the import and stay frozen. A listing moves,
so that kept the figure old rather than honest; a caller who has just looked
at it can now type what it reads today.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

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
def test_a_caller_can_type_the_whole_google_summary(as_caller, db, professions):
    p = _published(db, professions)

    r = as_caller.patch(f"/api/professionals/{p.id}", json={
        "rating": 4.9, "review_count": 222, "rating_captured_on": "2026-09-06",
    })
    assert r.status_code == 200, r.text

    body = _profile(as_caller)
    assert body["rating"] == 4.9
    assert body["review_count"] == 222
    assert body["rating_source"] == "Google"
    assert body["rating_captured_on"] == "2026-09-06"


def test_a_caller_can_correct_a_figure_that_has_moved(as_caller, db, professions):
    """The reason the edit exists: a listing's score drifts and its count climbs."""
    p = _published(db, professions, rating=4.7, review_count=180,
                   rating_captured_on=date(2026, 1, 4))

    as_caller.patch(f"/api/professionals/{p.id}", json={
        "rating": 4.9, "review_count": 222, "rating_captured_on": "2026-09-06",
    })
    body = _profile(as_caller)
    assert (body["rating"], body["review_count"]) == (4.9, 222)
    assert body["rating_captured_on"] == "2026-09-06"


# ── the figure is bounded ────────────────────────────────────────────────────
def test_a_score_above_five_is_refused(as_caller, db, professions):
    p = _published(db, professions)
    r = as_caller.patch(f"/api/professionals/{p.id}", json={"rating": 6})
    assert r.status_code == 422


def test_a_negative_review_count_is_refused(as_caller, db, professions):
    p = _published(db, professions)
    r = as_caller.patch(f"/api/professionals/{p.id}", json={"review_count": -1})
    assert r.status_code == 422


def test_the_source_is_still_not_a_staff_edit(as_caller, db, professions):
    """It says which platform the number came from. Retyping a Google figure
    does not change that, so the field stays out of the update shape."""
    p = _published(db, professions, source="Google Maps")
    as_caller.patch(f"/api/professionals/{p.id}", json={"source": "Trustpilot"})
    db.refresh(p)
    assert p.source == "Google Maps"


# ── all three, or none ───────────────────────────────────────────────────────
def test_a_score_without_a_date_publishes_nothing(as_caller, db, professions):
    """A number with no day behind it is the thing this site refuses to print."""
    p = _published(db, professions)
    as_caller.patch(f"/api/professionals/{p.id}",
                    json={"rating": 4.9, "review_count": 222})
    body = _profile(as_caller)
    assert body["rating"] is None
    assert body["review_count"] is None
    assert body["rating_captured_on"] is None


def test_clearing_the_date_withdraws_the_google_figure(as_caller, db, professions):
    p = _published(db, professions, rating=4.9, review_count=222,
                   rating_captured_on=date(2026, 9, 6))
    assert _profile(as_caller)["review_count"] == 222

    as_caller.patch(f"/api/professionals/{p.id}", json={"rating_captured_on": None})
    body = _profile(as_caller)
    assert body["rating"] is None
    assert body["rating_captured_on"] is None


# ── the two kinds of stars never mix ─────────────────────────────────────────
def test_the_summary_wins_over_the_typed_reviews_without_touching_them(
    as_caller, db, professions,
):
    p = _published(db, professions)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4,
                  text="Careful with the paperwork.", source="via Google"))
    db.commit()

    # Two typed reviews' worth of nothing: the summary is Google's own count.
    as_caller.patch(f"/api/professionals/{p.id}", json={
        "rating": 4.9, "review_count": 222, "rating_captured_on": "2026-09-06",
    })

    body = _profile(as_caller)
    assert body["review_count"] == 222        # the summary, not the row count
    assert len(body["reviews"]) == 1          # the row is still there
    assert body["reviews"][0]["author"] == "Sarah M."


def test_without_a_date_the_typed_reviews_are_what_publishes(as_caller, db, professions):
    """The fallback, unchanged: no summary means the rows speak for themselves."""
    p = _published(db, professions, rating=4.9, review_count=222)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4, source="via Google"))
    db.commit()

    body = _profile(as_caller)
    assert body["rating"] == 4.0
    assert body["review_count"] == 1
    assert body["rating_source"] == "via Google"
    assert body["rating_captured_on"] is None


def test_editing_the_summary_leaves_the_review_rows_alone(as_caller, db, professions):
    p = _published(db, professions)
    db.add(Review(professional_id=p.id, author="Sarah M.", stars=4, source="via Google"))
    db.commit()
    before = db.query(Review).filter_by(professional_id=p.id).count()

    as_caller.patch(f"/api/professionals/{p.id}", json={
        "rating": 4.9, "review_count": 222, "rating_captured_on": "2026-09-06",
    })
    assert db.query(Review).filter_by(professional_id=p.id).count() == before
