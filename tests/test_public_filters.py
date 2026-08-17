"""The filters a buyer uses to choose someone, which had no tests at all.

Proven, not assumed: an agent review found a live mutation sitting in the
working tree — the language filter replaced with `pass` — and the entire
committed suite passed with it disabled. A buyer filtering for "English" would
have been shown Greek-only speakers, presented as English-speaking, and nothing
anywhere would have said so.

Region, profession and language are the three filters the product promises
(docs/product-spec.md), so each is tested in **both** directions: the matching
row comes back, and the non-matching row does not. A filter that returns
everything passes any test that only checks for the row it wanted.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import Professional, Stage


def _published(db, professions, **kw):
    base = dict(
        business_name="Papadopoulos & Partners", contact_name="Kostas P",
        email="k@example.com", city="Chania", region="Crete",
        languages=["English", "Greek"], profession_id=professions["lawyer"],
        slug=None, published=True, stage=Stage.signed,
        published_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    base.setdefault("slug", base["business_name"].lower().replace(" ", "-").replace("&", "and"))
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


@pytest.fixture
def directory(db, professions):
    """Two professionals differing on every filterable axis."""
    crete = _published(db, professions, business_name="Crete Law", contact_name="Crete Law",
                       city="Chania", region="Crete",
                       languages=["English", "Greek"],
                       profession_id=professions["lawyer"])
    athens = _published(db, professions, business_name="Athens Notary", contact_name="Athens Notary",
                        city="Athens", region="Attica",
                        languages=["Greek"],
                        profession_id=professions["agent"])
    return crete, athens


def names(response) -> set[str]:
    return {item["name"] for item in response.json()["items"]}


# ── each filter, in both directions ─────────────────────────────────────────
@pytest.mark.parametrize("query, keeps, drops", [
    ("region=Crete", "Crete Law", "Athens Notary"),
    ("region=Attica", "Athens Notary", "Crete Law"),
    ("city=Chania", "Crete Law", "Athens Notary"),
    ("city=Athens", "Athens Notary", "Crete Law"),
    ("role=lawyer", "Crete Law", "Athens Notary"),
    ("role=agent", "Athens Notary", "Crete Law"),
    ("language=English", "Crete Law", "Athens Notary"),
])
def test_a_filter_keeps_the_match_and_drops_the_rest(query, keeps, drops, client, directory):
    """Both halves matter. A filter that has stopped filtering still returns
    the row you were looking for — that is exactly how a disabled language
    filter passed 186 tests."""
    r = client.get(f"/api/public/professionals?{query}")
    assert r.status_code == 200, r.text
    found = names(r)
    assert keeps in found, f"{query} lost the row it should have kept"
    assert drops not in found, f"{query} did not actually filter — {drops} came back"
    assert r.json()["total"] == 1, "total must count the filtered set, not everything"


def test_a_language_both_speak_returns_both(client, directory):
    """The array membership test, not a string comparison."""
    assert names(client.get("/api/public/professionals?language=Greek")) == {
        "Crete Law", "Athens Notary",
    }


def test_filters_combine(client, directory):
    assert names(client.get("/api/public/professionals?region=Crete&role=lawyer")) == {"Crete Law"}
    # A combination nothing satisfies must return nothing, not fall back to all.
    assert names(client.get("/api/public/professionals?region=Crete&role=agent")) == set()


@pytest.mark.parametrize("query", [
    "region=Nowhere", "city=Nowhere", "role=nosuchprofession", "language=Klingon",
])
def test_a_filter_matching_nothing_returns_nothing(query, client, directory):
    """The failure mode worth naming: an unknown value that quietly matches
    everything is worse than an empty page, because it looks like an answer."""
    r = client.get(f"/api/public/professionals?{query}")
    assert r.status_code == 200
    assert r.json() == {"total": 0, "items": []}


def test_an_unpublished_professional_is_never_returned(client, db, professions, directory):
    _published(db, professions, business_name="Not Live", contact_name="Not Live", published=False,
               slug="not-live", city="Chania", region="Crete")
    assert "Not Live" not in names(client.get("/api/public/professionals"))
    assert "Not Live" not in names(client.get("/api/public/professionals?city=Chania"))


# ── pagination ──────────────────────────────────────────────────────────────
def test_pagination_walks_the_whole_set_without_repeating(client, db, professions):
    for i in range(5):
        _published(db, professions, business_name=f"Firm {i}", contact_name=f"Firm {i}", slug=f"firm-{i}")

    first = client.get("/api/public/professionals?limit=2").json()
    second = client.get("/api/public/professionals?limit=2&offset=2").json()
    third = client.get("/api/public/professionals?limit=2&offset=4").json()

    # `total` is the size of the whole filtered set, not of the page.
    assert first["total"] == second["total"] == third["total"] == 5
    assert [len(p["items"]) for p in (first, second, third)] == [2, 2, 1]

    seen = [i["name"] for p in (first, second, third) for i in p["items"]]
    assert len(set(seen)) == 5, "a page repeated or skipped a professional"


def test_pagination_keeps_the_filter_applied(client, db, professions, directory):
    """A second page that quietly drops the filter shows a buyer the people
    they explicitly excluded."""
    for i in range(3):
        _published(db, professions, business_name=f"Crete Firm {i}", contact_name=f"Crete Firm {i}", slug=f"crete-{i}",
                   city="Chania", region="Crete", languages=["English"])

    page = client.get("/api/public/professionals?region=Crete&limit=2&offset=2")
    assert page.json()["total"] == 4
    assert "Athens Notary" not in names(page)


@pytest.mark.parametrize("query, expected", [
    ("limit=-1", 422),
    ("limit=0", 422),
    ("offset=-5", 422),
    ("limit=1000", 422),
])
def test_a_nonsense_page_size_is_refused_not_a_500(query, expected, client, directory):
    """Postgres rejects a negative LIMIT, so this used to be an unhandled 500
    on a public endpoint that anyone can call."""
    assert client.get(f"/api/public/professionals?{query}").status_code == expected
