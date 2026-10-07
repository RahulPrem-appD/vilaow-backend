"""Hand corrections to the website's text (client change requests, 5 October,
item 10: "a simple way to correct a translation by hand").

The website reads one language's corrections with every page, so that read is
public — and must carry nothing but the words. Changing one changes what every
visitor reads, so only an owner may.
"""
from __future__ import annotations


def _save(client, key="msg.nav_how", locale="el", text="Πώς λειτουργεί", source_hash="d6f7d110"):
    return client.put("/api/site-texts", json={
        "key": key, "locale": locale, "text": text, "source_hash": source_hash,
    })


def test_the_website_reads_one_languages_corrections(as_owner, client):
    assert _save(as_owner).status_code == 200
    assert _save(as_owner, key="msg.nav_who", locale="fr", text="Qui nous sommes").status_code == 200
    client.cookies.clear()

    r = client.get("/api/public/site-texts", params={"locale": "el"})
    assert r.status_code == 200
    assert r.json() == {"locale": "el", "texts": {"msg.nav_how": "Πώς λειτουργεί"}}
    assert r.headers["cache-control"] == "public, max-age=60"
    # Nothing about who made it travels to the public.
    assert "updated_by" not in r.text and "source_hash" not in r.text


def test_an_unknown_language_is_refused(client):
    assert client.get("/api/public/site-texts", params={"locale": "de"}).status_code == 422


def test_saving_again_replaces_the_correction(as_owner):
    _save(as_owner, text="Πώς δουλεύει")
    saved = _save(as_owner, text="Πώς λειτουργεί").json()
    assert saved["text"] == "Πώς λειτουργεί"
    assert saved["updated_by"] == "Owner"
    rows = as_owner.get("/api/site-texts").json()
    assert [(r["key"], r["locale"], r["text"]) for r in rows] == [
        ("msg.nav_how", "el", "Πώς λειτουργεί"),
    ]


def test_a_translation_must_say_which_english_it_followed(as_owner):
    r = _save(as_owner, source_hash=None)
    assert r.status_code == 422
    # English is the source itself, so it needs none and keeps none.
    english = _save(as_owner, locale="en", text="How it works", source_hash="abc12345").json()
    assert english["source_hash"] is None


def test_only_an_owner_may_change_the_text(as_caller, client):
    assert _save(as_caller).status_code == 403
    assert as_caller.delete("/api/site-texts", params={"key": "msg.nav_how", "locale": "el"}).status_code == 403
    # Any staff member may read the list.
    assert as_caller.get("/api/site-texts").status_code == 200
    client.cookies.clear()
    assert _save(client).status_code == 401
    assert client.get("/api/site-texts").status_code == 401


def test_removing_a_correction_goes_back_to_the_stored_text(as_owner, client):
    _save(as_owner)
    r = as_owner.delete("/api/site-texts", params={"key": "msg.nav_how", "locale": "el"})
    assert r.status_code == 204
    # Removing what is not there is not an error.
    assert as_owner.delete("/api/site-texts", params={"key": "msg.nav_how", "locale": "el"}).status_code == 204
    assert client.get("/api/public/site-texts", params={"locale": "el"}).json()["texts"] == {}


def test_a_key_is_a_key(as_owner):
    assert _save(as_owner, key="").status_code == 422
    assert _save(as_owner, key="msg.nav\nhow").status_code == 422
    assert _save(as_owner, key=" msg.nav_how").status_code == 422
    assert _save(as_owner, key="x" * 301).status_code == 422
    # A term's key is its English, punctuation and Greek letters included.
    assert _save(as_owner, key="term.Planning & Licensing (Πολεοδομία)").status_code == 200


def test_a_very_long_text_is_refused(as_owner):
    assert _save(as_owner, text="α" * 20_001).status_code == 422
