"""The owner-defined form, and the gate in front of the public site.

Written the same way as test_public.py: as leaks to prove impossible rather
than features to prove present. Owner-defined fields are the one mechanism that
could put arbitrary staff-entered data on a public page, so most of what is
below is an attempt to get an internal value out through the public API.

The publish endpoint had no test at all before this file — the public tests set
published=True straight in the database, so every gate in front of it was
unverified.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.models import Agreement, Professional, ProfessionField, Stage


# ── helpers ─────────────────────────────────────────────────────────────────
def _pro(db, professions, **kw):
    base = dict(
        business_name="Papadopoulos & Partners",
        contact_name="Kostas Papadopoulos",
        city="Heraklion", region="Crete",
        profession_id=professions["lawyer"],
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _field(db, profession_id, key, **kw):
    base = dict(
        profession_id=profession_id, key=key, label=key.replace("_", " ").title(),
        type="short_text", required=False, public=False, position=0, active=True,
    )
    base.update(kw)
    f = ProfessionField(**base)
    db.add(f)
    db.commit()
    return f


def _complete_agreement(db, professional_id):
    """Signed *and* confirmed — what the gate actually requires."""
    now = datetime.now(timezone.utc)
    a = Agreement(
        professional_id=professional_id, token=f"tok-{professional_id}",
        terms_version="2026-01", terms_text="1. ...", signed_at=now,
        email_verified_at=now, signed_name="Kostas P",
        signature_image="data:image/png;base64,x",
    )
    db.add(a)
    db.commit()
    return a


# ── the public boundary ─────────────────────────────────────────────────────
def test_an_internal_field_never_reaches_the_public_profile(client, db, professions):
    """The whole reason fields default to internal."""
    _field(db, professions["lawyer"], "best_time_to_call", public=False)
    _pro(db, professions, slug="kostas", published=True, stage=Stage.signed,
         custom={"best_time_to_call": "Tuesdays after 6pm"})

    body = client.get("/api/public/professionals/kostas").text
    assert "Tuesdays after 6pm" not in body
    assert "best_time_to_call" not in body


def test_switching_a_field_to_internal_removes_it_from_the_public_profile(
    as_owner, client, db, professions
):
    """A field can be made public by mistake. Un-ticking it has to actually
    take the value off the site, not merely stop new ones appearing."""
    f = _field(db, professions["lawyer"], "bar_number", public=True)
    _pro(db, professions, slug="kostas", published=True, stage=Stage.signed,
         custom={"bar_number": "BAR-4471"})

    assert "BAR-4471" in client.get("/api/public/professionals/kostas").text

    r = as_owner.patch(f"/api/professions/{professions['lawyer']}/fields/{f.id}",
                       json={"public": False})
    assert r.status_code == 200, r.text
    assert "BAR-4471" not in client.get("/api/public/professionals/kostas").text


def test_a_deleted_field_takes_its_value_off_the_site(as_owner, client, db, professions):
    """Deleting the definition is enough: the public serialiser is driven by
    definitions, so an orphaned value in the JSON has no way to be rendered."""
    f = _field(db, professions["lawyer"], "bar_number", public=True)
    p = _pro(db, professions, slug="kostas", published=True, stage=Stage.signed,
             custom={"bar_number": "BAR-4471"})

    assert as_owner.delete(
        f"/api/professions/{professions['lawyer']}/fields/{f.id}").status_code == 204

    assert "BAR-4471" not in client.get("/api/public/professionals/kostas").text
    # The value is still on the record — this is not a data-deletion path.
    db.refresh(p)
    assert p.custom["bar_number"] == "BAR-4471"


def test_an_uploaded_document_is_never_public(client, db, professions):
    """File fields answer with an asset id. Publishing that would be
    meaningless to a reader and an invitation to probe the endpoint."""
    _field(db, professions["lawyer"], "insurance_doc", type="file", public=True)
    _pro(db, professions, slug="kostas", published=True, stage=Stage.signed,
         custom={"insurance_doc": 7})

    body = client.get("/api/public/professionals/kostas").json()
    assert "insurance_doc" not in str(body)


# ── writing answers ─────────────────────────────────────────────────────────
def test_a_value_outside_the_allowed_options_is_refused(as_caller, db, professions):
    _field(db, professions["lawyer"], "specialism", type="select",
           options=["Conveyancing", "Litigation"])
    p = _pro(db, professions)

    r = as_caller.patch(f"/api/professionals/{p.id}",
                        json={"custom": {"specialism": "Astrology"}})
    assert r.status_code == 422, r.text


def test_unknown_keys_are_dropped_rather_than_stored(as_caller, db, professions):
    """A value with no field to describe it can never be validated or
    rendered, so keeping it would only be somewhere for junk to accumulate."""
    _field(db, professions["lawyer"], "bar_number")
    p = _pro(db, professions)

    r = as_caller.patch(f"/api/professionals/{p.id}", json={
        "custom": {"bar_number": "BAR-1", "not_a_field": "junk"}})
    assert r.status_code == 200, r.text

    db.refresh(p)
    assert p.custom == {"bar_number": "BAR-1"}


def test_submitting_one_answer_does_not_wipe_the_others(as_caller, db, professions):
    _field(db, professions["lawyer"], "bar_number")
    _field(db, professions["lawyer"], "vat_id")
    p = _pro(db, professions, custom={"bar_number": "BAR-1", "vat_id": "EL9"})

    as_caller.patch(f"/api/professionals/{p.id}", json={"custom": {"vat_id": "EL10"}})

    db.refresh(p)
    assert p.custom == {"bar_number": "BAR-1", "vat_id": "EL10"}


# ── the publish gate ────────────────────────────────────────────────────────
def test_publishing_needs_a_signed_agreement(as_owner, db, professions):
    """Clause 5 is the permission to show someone's name and photo at all."""
    p = _pro(db, professions, photo="https://example.com/p.jpg")
    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 409
    assert "not been signed" in r.text


def test_a_signature_with_an_unconfirmed_email_does_not_open_the_gate(
    as_owner, db, professions
):
    p = _pro(db, professions, photo="https://example.com/p.jpg")
    db.add(Agreement(professional_id=p.id, token="t1", terms_version="2026-01",
                     signed_at=datetime.now(timezone.utc)))   # no email_verified_at
    db.commit()

    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 409
    assert "not been confirmed" in r.text


def test_publishing_needs_a_photo(as_owner, db, professions):
    p = _pro(db, professions)
    _complete_agreement(db, p.id)
    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 409
    assert "No photo" in r.text


def test_publishing_needs_every_required_field(as_owner, db, professions):
    _field(db, professions["lawyer"], "bar_number", label="Bar number", required=True)
    p = _pro(db, professions, photo="https://example.com/p.jpg")
    _complete_agreement(db, p.id)

    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 409
    assert "Bar number is required" in r.text


def test_all_four_gates_satisfied_publishes(as_owner, db, professions):
    _field(db, professions["lawyer"], "bar_number", label="Bar number", required=True)
    p = _pro(db, professions, photo="https://example.com/p.jpg",
             custom={"bar_number": "BAR-4471"})
    _complete_agreement(db, p.id)

    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 200, r.text

    db.refresh(p)
    assert p.published is True
    assert p.slug


def test_a_caller_cannot_publish(as_caller, db, professions):
    p = _pro(db, professions, photo="https://example.com/p.jpg")
    _complete_agreement(db, p.id)
    assert as_caller.post(f"/api/professionals/{p.id}/publish").status_code == 403


# ── adding a requirement to a live profession ───────────────────────────────
def test_a_new_required_field_never_unpublishes_anyone(as_owner, client, db, professions):
    """The rule that keeps buyers off dead links: nothing disappears from the
    public site when the owner tightens a form. It raises a backlog instead."""
    p = _pro(db, professions, slug="kostas", published=True, stage=Stage.signed,
             photo="https://example.com/p.jpg")
    _complete_agreement(db, p.id)

    r = as_owner.post(f"/api/professions/{professions['lawyer']}/fields", json={
        "key": "insurance_expiry", "label": "Insurance expiry",
        "type": "date", "required": True})
    assert r.status_code == 201, r.text

    # Still live, still reachable.
    assert client.get("/api/public/professionals/kostas").status_code == 200
    db.refresh(p)
    assert p.published is True

    # But the gap is visible to staff rather than silently ignored.
    state = as_owner.get(f"/api/professionals/{p.id}/readiness").json()
    assert state["ready"] is False
    assert "insurance_expiry" in state["missing_field_keys"]


# ── who may build the form ──────────────────────────────────────────────────
def test_only_an_owner_defines_fields(as_caller, db, professions):
    r = as_caller.post(f"/api/professions/{professions['lawyer']}/fields", json={
        "key": "bar_number", "label": "Bar number", "type": "short_text"})
    assert r.status_code == 403


def test_a_choice_field_needs_options(as_owner, db, professions):
    """A select with no options is a field nobody can ever fill in."""
    r = as_owner.post(f"/api/professions/{professions['lawyer']}/fields", json={
        "key": "specialism", "label": "Specialism", "type": "select", "options": []})
    assert r.status_code == 422


def test_a_field_cannot_be_edited_through_another_profession(as_owner, db, professions):
    """/professions/1/fields/99 must not reach a field belonging to profession 2."""
    f = _field(db, professions["lawyer"], "bar_number")
    r = as_owner.patch(f"/api/professions/{professions['agent']}/fields/{f.id}",
                       json={"label": "Hijacked"})
    assert r.status_code == 404
