"""The order the directory lists people in.

Sorted by rating, the same few professionals held the top of the page on every
visit, and whoever sat below them waited for leads that never came. The client
asked for a random order instead, so the homepage sends a seed: one number that
fixes one shuffle. The same seed must give the same order — that is what lets
page 2 continue page 1 rather than reshuffle and show some people twice and
others never — and different seeds must genuinely move people, or it is not a
shuffle at all.

Without a seed the order is still by rating; that is covered in
test_staff_reviews.py.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import Professional, Stage

HOW_MANY = 12


@pytest.fixture
def directory(db, professions):
    """Twelve published professionals, returned as their slugs, sorted."""
    now = datetime.now(timezone.utc)
    slugs = [f"pro-{n:02d}" for n in range(HOW_MANY)]
    for slug in slugs:
        db.add(Professional(
            business_name=slug, contact_name=slug, slug=slug,
            city="Heraklion", region="Crete",
            profession_id=professions["lawyer"],
            published=True, stage=Stage.signed, published_at=now,
        ))
    db.commit()
    return slugs


def _order(client, **params):
    response = client.get("/api/public/professionals", params=params)
    assert response.status_code == 200, response.text
    return [item["slug"] for item in response.json()["items"]]


def test_one_seed_gives_the_same_order_every_time(client, directory):
    assert _order(client, seed=7) == _order(client, seed=7)


def test_paging_through_one_seed_shows_everyone_exactly_once(client, directory):
    pages = [_order(client, seed=7, limit=5, offset=offset) for offset in (0, 5, 10)]
    seen = [slug for page in pages for slug in page]
    assert sorted(seen) == directory        # nobody missing, nobody twice


def test_different_seeds_put_different_people_first(client, directory):
    """The client's actual complaint: the same few always on top."""
    firsts = {_order(client, seed=seed, limit=1)[0] for seed in range(40)}
    assert len(firsts) >= HOW_MANY // 2


def test_a_seed_moves_people_away_from_the_unseeded_order(client, directory):
    unseeded = _order(client)
    assert any(_order(client, seed=seed) != unseeded for seed in range(3))


def test_the_count_does_not_change_with_the_seed(client, directory):
    total = client.get("/api/public/professionals", params={"seed": 7}).json()["total"]
    assert total == HOW_MANY


@pytest.mark.parametrize("seed", ["-1", "2147483648", "abc", "1.5"])
def test_a_bad_seed_is_refused_rather_than_a_500(client, directory, seed):
    response = client.get("/api/public/professionals", params={"seed": seed})
    assert response.status_code == 422
