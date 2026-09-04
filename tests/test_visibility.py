"""Which built-in fields a public page may show, and who decides.

The owner-defined form always had its per-field `public` flag; the built-in
fields were hardcoded — a profile showed its photo, its bio, its rating
whenever they had a value, and nothing could turn one off. `visible_fields` is
that lever, as an allow list at two levels: a profession's list is the default
for everyone in it, and a professional's wins over it in either direction.
The tests below pin the properties it must not lose:

  * a record nobody has touched publishes exactly what it published before the
    columns existed — the guarantee the whole change rests on, asserted first.
  * withholding is the only direction it moves in. It changes no other field
    and never turns a None into a value; the things it can publish that were
    not published before — a licence number, a trust badge — need their key
    switched on deliberately.
  * a field left out is left out everywhere at once. A rating kept off the
    profile but printed in the directory would be a half-measure.
  * the decision itself stays staff-facing: never in a public response, while
    the admin endpoints still return the real values, because staff must see
    what a buyer cannot.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.errors import Invalid
from app.domain.visibility import (
    DEFAULT_VISIBLE,
    FIELD_KEYS,
    OFF_BY_DEFAULT,
    resolve,
    validate,
)
from app.models import FieldType, Profession, Professional, ProfessionField, Review, Stage

# Every field the page can carry, dressed — so "everything else unchanged" can
# be asserted field by field rather than by spot-checking two or three.
# license and vat_number are in here so the untouched-record test can prove
# those two come back null even with values sitting behind them.
FULL = dict(
    photo="https://example.com/kostas.jpg",
    subrole="Conveyancing lawyer",
    coverage="All of Crete",
    verified_year=2025,
    bio="Fifteen years of purchases and title checks.",
    languages=["English", "Greek"],
    highlights=["Fixed fee", "Terms in English"],
    years=15,
    specialties=["Property law", "Golden Visa"],
    education="University of Athens",
    costs=[{"item": "Purchase", "amount": "€900"}],
    cost_note="VAT included",
    faq=[{"q": "Do you work in English?", "a": "Yes."}],
    license="BAR-99",
    vat_number="EL123456789",
)


def _showing(*without):
    """The default list minus a few keys — "today's page, without these"."""
    return [k for k in FIELD_KEYS if k in DEFAULT_VISIBLE and k not in without]


def _pro(db, professions, **kw):
    base = dict(
        business_name="Papadopoulos & Partners",
        contact_name="Kostas Papadopoulos",
        phone="+30 2810 555 999",           # internal: never public
        email="kostas@example.com",         # internal: never public
        city="Heraklion", region="Crete",
        profession_id=professions["lawyer"],
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _published(db, professions, **kw):
    kw.setdefault("slug", "kostas")
    return _pro(db, professions, published=True, stage=Stage.signed,
                published_at=datetime.now(timezone.utc), **kw)


def _reviewed(db, professions, **kw):
    """A published professional with one attributed review behind the rating."""
    p = _published(db, professions, **kw)
    db.add(Review(professional_id=p.id, author="G.", stars=5, source="via Google"))
    db.commit()
    return p


def _public_field(db, professions):
    db.add(ProfessionField(profession_id=professions["lawyer"], key="bar_number",
                           label="Bar number", type=FieldType.short_text,
                           required=False, public=True, active=True, position=0))
    db.commit()


def profile(client, slug="kostas"):
    return client.get(f"/api/public/professionals/{slug}").json()


def card(client, slug="kostas"):
    items = client.get("/api/public/professionals").json()["items"]
    return next(i for i in items if i["slug"] == slug)


# ── the record nobody has decided anything about ─────────────────────────────
def test_an_untouched_record_publishes_what_it_publishes_today(client, db, professions):
    """Both columns null. Everything below rests on this: exactly the payload
    the site served before the columns existed, with the two never-published
    columns still null even though both have values."""
    _public_field(db, professions)
    _reviewed(db, professions, custom=dict(bar_number="BAR-4471"), **FULL)

    r = profile(client)
    for key, value in FULL.items():
        if key in ("license", "vat_number"):
            assert r[key] is None, key
        else:
            assert r[key] == value, key
    assert r["rating"] == 5.0
    assert r["review_count"] == 1
    assert r["rating_source"] == "via Google"
    assert [review["author"] for review in r["reviews"]] == ["G."]
    assert [d["key"] for d in r["details"]] == ["bar_number"]

    item = card(client)
    assert item["photo"] == FULL["photo"]
    assert item["years"] == FULL["years"]
    assert item["languages"] == FULL["languages"]
    assert item["verified_year"] == FULL["verified_year"]
    assert item["rating"] == 5.0
    assert item["review_count"] == 1
    assert item["rating_source"] == "via Google"


def test_omitting_bio_blanks_bio_and_touches_nothing_else(client, db, professions):
    """The lever's narrow guarantee: withholding changes no other field, so
    the rest of the payload is byte-for-byte the untouched one."""
    _reviewed(db, professions, slug="undecided", **FULL)
    _reviewed(db, professions, slug="quiet", visible_fields=_showing("bio"), **FULL)

    a, b = profile(client, "undecided"), profile(client, "quiet")
    assert b["bio"] is None
    for compared in (a, b):
        del compared["bio"], compared["slug"]
    assert b == a


# ── the rating, and the words under it ───────────────────────────────────────
def test_omitting_rating_blanks_the_whole_trio_on_the_profile_and_the_card(
    client, db, professions,
):
    """Stars, count and attribution are one claim, so one key takes all three
    — in both places a buyer can look."""
    _reviewed(db, professions, visible_fields=_showing("rating"), **FULL)

    r = profile(client)
    assert r["rating"] is None
    assert r["review_count"] is None
    assert r["rating_source"] is None
    # The written reviews are a separate claim and survive the hidden stars.
    assert [review["author"] for review in r["reviews"]] == ["G."]

    item = card(client)
    assert item["rating"] is None
    assert item["review_count"] is None
    assert item["rating_source"] is None


def test_omitting_reviews_empties_the_list_and_leaves_the_stars(client, db, professions):
    """The other half of the separation: a professional who stands by the
    average but not the words keeps one and drops the other."""
    _reviewed(db, professions, visible_fields=_showing("reviews"), **FULL)

    r = profile(client)
    assert r["reviews"] == []
    assert r["rating"] == 5.0
    assert r["review_count"] == 1
    assert r["rating_source"] == "via Google"


# ── one block, withheld together ────────────────────────────────────────────
def test_omitting_costs_blanks_the_table_and_its_note_together(client, db, professions):
    """The note annotates the prices; left behind on its own it would be a
    note about nothing."""
    _published(db, professions, visible_fields=_showing("costs"), **FULL)

    r = profile(client)
    assert r["costs"] is None
    assert r["cost_note"] is None
    assert r["bio"] == FULL["bio"]


# ── the card carries less, and withholds the same ───────────────────────────
@pytest.mark.parametrize("key", ["photo", "years", "languages", "verified_year"])
def test_a_card_field_left_out_is_absent_from_the_card_and_the_profile(
    client, db, professions, key,
):
    _published(db, professions, visible_fields=_showing(key), **FULL)

    assert card(client)[key] is None
    assert profile(client)[key] is None


# ── the two columns that were never published ────────────────────────────────
@pytest.mark.parametrize("key", ["license", "vat_number"])
def test_switching_one_on_publishes_it_and_leaves_the_other_off(client, db, professions, key):
    """The one direction this lever can publish something new, and it takes a
    deliberate decision: neither key is in the default."""
    _published(db, professions, visible_fields=[*DEFAULT_VISIBLE, key], **FULL)

    r = profile(client)
    other = "vat_number" if key == "license" else "license"
    assert r[key] == FULL[key]
    assert r[other] is None


@pytest.mark.parametrize("key", ["license", "vat_number"])
def test_the_two_numbers_never_reach_a_listing_card(client, db, professions, key):
    """A buyer who wants the numbers is reading the profile. The card is a
    skimming surface, and these two stay off it whatever anyone switches on."""
    _reviewed(db, professions, visible_fields=[*DEFAULT_VISIBLE, key], **FULL)

    assert key not in card(client)


# ── the three trust badges ───────────────────────────────────────────────────
def test_a_record_nobody_has_decided_about_publishes_no_badges(client, db, professions):
    """The three badge keys are in OFF_BY_DEFAULT, so an untouched record —
    and every record already sitting in the database — publishes an empty
    list, on the card and the profile alike: the badges were claims made for
    everyone until now, and this change makes none of them."""
    _reviewed(db, professions, **FULL)

    assert profile(client)["badges"] == []
    assert card(client)["badges"] == []


def test_switching_a_badge_on_publishes_its_word_and_moves_nothing_else(
    client, db, professions,
):
    """The lever's narrow guarantee, held for the badges too: a ticked key
    adds its one word to the payload and changes nothing else about the
    record, byte for byte."""
    _reviewed(db, professions, slug="undecided", **FULL)
    _reviewed(db, professions, slug="badged",
              visible_fields=[*_showing(), "badge_licensed"], **FULL)

    a, b = profile(client, "undecided"), profile(client, "badged")
    assert b["badges"] == ["licensed"]
    assert card(client, "badged")["badges"] == ["licensed"]
    for compared in (a, b):
        del compared["badges"], compared["slug"]
    assert b == a


def test_all_three_badges_publish_in_the_cards_order_not_the_stored_one(
    client, db, professions,
):
    """The stored order is an accident of how the list was written; the card
    prints its own. `validate` would impose it on the way in, but a list
    written straight to the column must not be able to reorder the page."""
    _reviewed(db, professions,
              visible_fields=["badge_interviewed", *_showing(),
                              "badge_insured", "badge_licensed"],
              **FULL)

    assert profile(client)["badges"] == ["licensed", "insured", "interviewed"]
    assert card(client)["badges"] == ["licensed", "insured", "interviewed"]


def test_a_profession_can_turn_a_badge_on_and_a_record_still_wins(
    client, db, professions,
):
    """The badges ride the same two levels as every other key. A trade can
    grant one across the board, and one professional can still decide the
    other way — dropping a badge its trade granted, or claiming one it
    did not."""
    granted = db.get(Profession, professions["lawyer"])
    granted.visible_fields = [*DEFAULT_VISIBLE, "badge_insured"]
    withheld = db.get(Profession, professions["engineer"])
    withheld.visible_fields = list(DEFAULT_VISIBLE)
    db.commit()

    _reviewed(db, professions, slug="follows", **FULL)
    _reviewed(db, professions, slug="opts-out",
              visible_fields=list(DEFAULT_VISIBLE), **FULL)
    _reviewed(db, professions, slug="opts-in",
              profession_id=professions["engineer"],
              visible_fields=[*DEFAULT_VISIBLE, "badge_licensed"], **FULL)

    assert card(client, "follows")["badges"] == ["insured"]
    assert card(client, "opts-out")["badges"] == []
    assert card(client, "opts-in")["badges"] == ["licensed"]


# ── the profession's list, and who wins ─────────────────────────────────────
def test_a_professions_list_governs_a_professional_who_has_none_of_its_own(
    client, db, professions,
):
    """The default for everyone in it, applied where it should be: the record
    itself has made no decision, so it inherits."""
    profession = db.get(Profession, professions["lawyer"])
    profession.visible_fields = _showing("bio", "rating")
    db.commit()
    _reviewed(db, professions, **FULL)

    r = profile(client)
    assert r["bio"] is None
    assert r["rating"] is None
    item = card(client)
    assert item["rating"] is None
    assert item["photo"] == FULL["photo"]


def test_a_professionals_list_wins_over_its_professions_in_both_directions(
    client, db, professions,
):
    """The narrower level decides: one record can show a field its profession
    hides, and hide one it shows."""
    profession = db.get(Profession, professions["lawyer"])
    profession.visible_fields = [k for k in FIELD_KEYS if k != "bio"]  # bio off, license on
    db.commit()
    _published(db, professions, visible_fields=list(DEFAULT_VISIBLE), **FULL)  # the reverse

    r = profile(client)
    assert r["bio"] == FULL["bio"], "the profession hid it; the professional decided otherwise"
    assert r["license"] is None, "the profession showed it; the professional decided otherwise"


def test_an_empty_list_shows_nothing_and_is_not_read_as_inheritance(
    client, db, professions,
):
    """[] is a storable answer meaning "show none of them". Treating it as an
    absence would inherit the profession's list — here deliberately set — so
    the empty list has to win over it."""
    _public_field(db, professions)
    profession = db.get(Profession, professions["lawyer"])
    profession.visible_fields = list(DEFAULT_VISIBLE)
    db.commit()
    p = _reviewed(db, professions, visible_fields=[],
                  custom=dict(bar_number="BAR-4471"), **FULL)

    r = profile(client)
    for key in FULL:
        assert r[key] is None, key
    assert r["rating"] is None
    assert r["review_count"] is None
    assert r["rating_source"] is None
    assert r["reviews"] == []
    assert r["details"] == []
    # What is left standing is the page's scaffolding, not a field key.
    assert r["slug"] == p.slug
    assert r["name"] == "Kostas Papadopoulos"
    assert r["city"] == "Heraklion"

    item = card(client)
    assert item["photo"] is None
    assert item["rating"] is None


def test_the_decision_itself_never_appears_in_a_public_response(client, db, professions):
    """Which fields a buyer does not see is an editorial decision, not
    something a buyer needs to read. The badges are the one effect that does
    travel — and they travel as their own words, never as the keys or the
    list that chose them."""
    _reviewed(db, professions,
              visible_fields=[*_showing("bio", "rating"), "badge_licensed"], **FULL)

    r, item = profile(client), card(client)
    assert "visible_fields" not in r
    assert "visible_fields" not in item
    assert r["badges"] == item["badges"] == ["licensed"]
    assert "badge_licensed" not in r
    assert "badge_licensed" not in item


# ── writing the list ────────────────────────────────────────────────────────
def test_an_unknown_key_is_refused_and_named(as_caller, client, db, professions):
    """`phone` is the sharpest case: it is not in the vocabulary because a
    buyer never gets a direct contact route. Accepting it would make this a
    lever on what is published rather than on what is shown."""
    p = _published(db, professions)

    r = as_caller.patch(f"/api/professionals/{p.id}",
                        json={"visible_fields": ["bio", "phone"]})
    assert r.status_code == 422, r.text
    assert "phone" in r.text

    db.refresh(p)
    assert p.visible_fields is None, "a refused list must not be half-stored"


def test_a_valid_list_round_trips_in_vocabulary_order(as_caller, client, db, professions):
    p = _published(db, professions)

    r = as_caller.patch(f"/api/professionals/{p.id}",
                        json={"visible_fields": ["years", "bio", "years"]})
    assert r.status_code == 200, r.text
    assert r.json()["visible_fields"] == ["bio", "years"]  # deduplicated, FIELD_KEYS order

    # And the admin endpoint reads the same thing back.
    assert as_caller.get(f"/api/professionals/{p.id}").json()["visible_fields"] == ["bio", "years"]

    # Null goes back to inheriting the profession's list.
    r = as_caller.patch(f"/api/professionals/{p.id}", json={"visible_fields": None})
    assert r.status_code == 200, r.text
    assert r.json()["visible_fields"] is None


def test_a_professions_list_is_validated_the_same_way(as_owner, client, db, professions):
    r = as_owner.patch(f"/api/professions/{professions['lawyer']}",
                       json={"visible_fields": ["bio", "witchcraft"]})
    assert r.status_code == 422, r.text
    assert "witchcraft" in r.text

    r = as_owner.post("/api/professions",
                      json={"key": "notary", "label": "Notary", "plural": "Notaries",
                            "visible_fields": ["years", "photo", "years"]})
    assert r.status_code == 201, r.text
    assert r.json()["visible_fields"] == ["photo", "years"]

    r = as_owner.patch(f"/api/professions/{professions['lawyer']}",
                       json={"visible_fields": ["bio"]})
    assert r.status_code == 200, r.text
    assert r.json()["visible_fields"] == ["bio"]


# ── staff still see what a buyer cannot ─────────────────────────────────────
def test_the_admin_record_still_carries_the_real_values(as_caller, client, db, professions):
    """The lever acts on the public payload, not on the record. A caller
    editing a profile must see the bio and the phone number they are deciding
    what to do with, or they are editing blind."""
    p = _reviewed(db, professions, visible_fields=[], **FULL)

    body = as_caller.get(f"/api/professionals/{p.id}").json()
    assert body["bio"] == FULL["bio"]
    assert body["photo"] == FULL["photo"]
    assert body["license"] == FULL["license"]
    assert body["phone"] == "+30 2810 555 999"
    assert body["visible_fields"] == []

    # While the buyer's copy of the same person is the withheld one.
    assert profile(client)["bio"] is None


# ── the rule itself, without a database ─────────────────────────────────────
def test_resolve_takes_the_professional_then_the_profession_then_the_default():
    assert resolve(None, None) == DEFAULT_VISIBLE
    assert resolve(["bio"], None) == {"bio"}
    assert resolve(None, ["bio"]) == {"bio"}
    assert resolve(["bio"], ["photo"]) == {"bio"}  # the narrower level wins


def test_an_empty_list_is_an_answer_not_an_absence():
    assert resolve([], ["bio"]) == set()
    assert resolve([], None) == set()


def test_validate_returns_the_list_in_vocabulary_order_deduplicated():
    assert validate(["years", "bio", "years"]) == ["bio", "years"]


def test_validate_names_the_first_key_outside_the_vocabulary():
    with pytest.raises(Invalid) as raised:
        validate(["bio", "witchcraft", "sorcery"])
    assert "witchcraft" in str(raised.value)


def test_the_default_is_everything_except_the_never_shown():
    """The reason OFF_BY_DEFAULT exists, pinned by name: these are the keys
    that have never been shown and must not start showing because somebody
    added them to a list. If this stops being true, an untouched database
    changes what it shows the day the change deploys — the one thing this
    design promised not to do."""
    assert OFF_BY_DEFAULT == frozenset((
        "license", "vat_number",
        "badge_licensed", "badge_insured", "badge_interviewed",
    ))
    assert frozenset(FIELD_KEYS) - DEFAULT_VISIBLE == OFF_BY_DEFAULT
