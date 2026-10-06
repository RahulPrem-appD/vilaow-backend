"""The town and island under a region (client change requests, 5 October).

A buyer who picks Crete sees everyone in Crete; picking Chania under it
narrows that to the people who serve Chania. "Serve" is the record's areas
served, and a record with none filled in falls back to its office town. Each
rule is checked in both directions, as the other filters are.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import Professional, Stage


def _published(db, professions, name, **kw):
    base = dict(
        business_name=name, contact_name=name, email=f"{name.lower().replace(' ', '')}@example.com",
        city="Chania", region="Crete", languages=["English"], profession_id=professions["lawyer"],
        slug=name.lower().replace(" ", "-"), published=True, stage=Stage.signed,
        published_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def names(client, query):
    response = client.get(f"/api/public/professionals?{query}")
    assert response.status_code == 200, response.text
    return {item["name"] for item in response.json()["items"]}


@pytest.fixture
def crete(db, professions):
    _published(db, professions, "Serves Chania", city="Heraklion", areas_served=["Chania"])
    _published(db, professions, "Serves Rethymno", city="Chania", areas_served=["Rethymno"])
    _published(db, professions, "Office In Chania", city="Chania", areas_served=None)
    _published(db, professions, "Office In Heraklion", city="Heraklion", areas_served=[])
    # Office in Athens, but works in Agios Nikolaos.
    _published(db, professions, "Athens Firm", city="Athens", region="Athens",
               areas_served=["Central Athens", "Agios Nikolaos & Elounda"])


def test_a_town_shows_who_serves_it(client, crete):
    assert names(client, "region=Crete&area=Chania") == {"Serves Chania", "Office In Chania"}


def test_areas_served_beat_the_office_town(client, crete):
    # Office in Chania, but they said they serve Rethymno only.
    assert "Serves Rethymno" not in names(client, "region=Crete&area=Chania")
    assert "Serves Rethymno" in names(client, "region=Crete&area=Rethymno")


def test_an_area_named_after_two_places_matches_its_town(client, db, professions):
    _published(db, professions, "Office In Agios Nikolaos", city="Agios Nikolaos")
    assert "Office In Agios Nikolaos" in names(
        client, "region=Crete&area=Agios%20Nikolaos%20%26%20Elounda")


def test_the_region_includes_anyone_serving_it(client, crete):
    everyone_in_crete = names(client, "region=Crete")
    assert "Athens Firm" in everyone_in_crete
    assert "Athens Firm" in names(client, "region=Athens")
    # Every town's list is part of its region's list.
    for town in ("Chania", "Rethymno", "Heraklion", "Agios%20Nikolaos%20%26%20Elounda"):
        assert names(client, f"region=Crete&area={town}") <= everyone_in_crete


def test_islands_are_areas(client, db, professions):
    _published(db, professions, "Paros Agent", city="Parikia", region="Aegean Islands",
               areas_served=["Paros", "Naxos"])
    assert names(client, "region=Aegean%20Islands") == {"Paros Agent"}
    assert names(client, "region=Aegean%20Islands&area=Naxos") == {"Paros Agent"}
    assert names(client, "region=Aegean%20Islands&area=Milos") == set()


def test_an_unknown_area_is_refused(client, crete):
    response = client.get("/api/public/professionals?region=Crete&area=Atlantis")
    assert response.status_code == 422
