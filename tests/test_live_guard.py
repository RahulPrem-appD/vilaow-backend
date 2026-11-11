"""A live profile stays the one the owner approved (app/services/live_guard.py).

On 11 November a caller typed a new agent's name and details into a profile
that had been live since a test signing on 30 August. They were public at
once, under someone else's signature, and its photo was gone. Each test below
is one of the eleven writes that could do that, or the gate that stops such a
profile going back up under the wrong signature.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.domain.visibility import DEFAULT_VISIBLE
from app.models import Agreement, Event, Professional, Stage

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 128
WITHOUT_PHOTO = sorted(DEFAULT_VISIBLE - {"photo"})

SIGN_BODY = {
    "signature_image": "data:image/png;base64,iVBORw0KGgo=",
    "licence": "ABC123",
    "vat_number": "EL123456789",
    "email": "k@example.com",
    "phone": "+30 210 111 1111",
    "agreed": True,
}


@pytest.fixture(autouse=True)
def uploads_in_tmp(tmp_path):
    from app.adapters.storage.local import LocalStorage
    from app.api.deps import get_storage
    from app.main import app
    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    yield
    app.dependency_overrides.pop(get_storage, None)


# ── helpers ─────────────────────────────────────────────────────────────────
def _record(db, professions, client, **kw):
    """An offline record with a real uploaded photo, so a save that sends the
    photo back passes the same check the editor's saves do."""
    base = dict(business_name="Harbour Real Estate - Chania",
                contact_name="Kostas Papadopoulos", city="Chania", region="Crete",
                email="k@example.com", profession_id=professions["agent"],
                stage=Stage.details_collected)
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    r = client.post(f"/api/professionals/{p.id}/photo",
                    files={"file": ("me.png", PNG, "image/png")})
    assert r.status_code == 201, r.text
    db.refresh(p)
    return p


def _complete(db, p, signed_name):
    now = datetime.now(timezone.utc)
    db.add(Agreement(professional_id=p.id, token=f"done-{p.id}-{signed_name}",
                     terms_version="2026-01", terms_text="1. ...", signed_at=now,
                     email_verified_at=now, signed_name=signed_name,
                     signature_image="data:image/png;base64,x"))
    db.commit()


def _go_live(db, p):
    """Where the real flow leaves a published profile: Signed, and a completed
    agreement under the name it shows."""
    _complete(db, p, p.contact_name)
    db.refresh(p)
    p.stage, p.published = Stage.signed, True
    p.published_at, p.slug = datetime.now(timezone.utc), f"live-{p.id}"
    db.commit()
    return p


def _live(db, professions, client, **kw):
    return _go_live(db, _record(db, professions, client, **kw))


def _taken_down(db, client, p) -> str:
    """Asserts `p` is off the site and returns why, as its history says."""
    db.expire_all()
    assert db.get(Professional, p.id).published is False
    assert client.get(f"/api/public/professionals/live-{p.id}").status_code == 404
    event = db.scalars(select(Event).where(Event.professional_id == p.id,
                                           Event.kind == "unpublished")).one()
    return event.detail


def _still_live(db, client, p) -> None:
    db.expire_all()
    assert db.get(Professional, p.id).published is True
    assert client.get(f"/api/public/professionals/live-{p.id}").status_code == 200
    assert not db.scalars(select(Event).where(Event.professional_id == p.id,
                                              Event.kind == "unpublished")).all()


def _events(db, p, kind) -> list[Event]:
    db.expire_all()
    return list(db.scalars(select(Event).where(Event.professional_id == p.id,
                                                Event.kind == kind)).all())


def _login(client, email, password):
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text


# ── the record editor: PATCH /api/professionals/{id} ────────────────────────
def test_renaming_a_live_profile_takes_it_offline(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    r = as_caller.patch(f"/api/professionals/{p.id}",
                        json={"contact_name": "GIORGOS NEOPOULOS"})
    assert r.status_code == 200, r.text
    assert r.json()["published"] is False

    reason = _taken_down(db, as_caller, p)
    assert "the name changed from Kostas Papadopoulos to GIORGOS NEOPOULOS" in reason
    renamed = _events(db, p, "renamed")
    assert [(e.detail, e.actor_label) for e in renamed] == [
        ("Kostas Papadopoulos → GIORGOS NEOPOULOS", "Caller")]


def test_a_change_of_capitals_or_spacing_is_not_a_new_name(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    as_caller.patch(f"/api/professionals/{p.id}", json={"contact_name": "KOSTAS  papadopoulos "})

    _still_live(db, as_caller, p)
    assert len(_events(db, p, "renamed")) == 1   # still recorded, just harmless


def test_saving_the_same_name_and_photo_keeps_it_live(as_caller, db, professions):
    """The editor sends every field on every save. A bio fix must not take
    a profile down just because the name and photo rode along unchanged."""
    p = _live(db, professions, as_caller)

    r = as_caller.patch(f"/api/professionals/{p.id}", json={
        "business_name": p.business_name, "contact_name": p.contact_name,
        "photo": p.photo, "visible_fields": None, "bio": "Thirty years in Chania.",
    })
    assert r.status_code == 200, r.text

    _still_live(db, as_caller, p)
    assert _events(db, p, "renamed") == []


def test_removing_the_photo_takes_it_offline(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    as_caller.patch(f"/api/professionals/{p.id}", json={"photo": None})

    assert "the photo was removed" in _taken_down(db, as_caller, p)
    assert [e.actor_label for e in _events(db, p, "photo_removed")] == ["Caller"]


def test_switching_the_photo_off_takes_it_offline(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    as_caller.patch(f"/api/professionals/{p.id}", json={"visible_fields": WITHOUT_PHOTO})

    assert "the photo was switched off" in _taken_down(db, as_caller, p)


def test_changing_the_profession_takes_it_offline(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    as_caller.patch(f"/api/professionals/{p.id}", json={"profession_id": professions["lawyer"]})

    assert "the profession changed" in _taken_down(db, as_caller, p)


def test_an_offline_record_is_only_recorded(as_caller, db, professions):
    p = _record(db, professions, as_caller)

    as_caller.patch(f"/api/professionals/{p.id}", json={"contact_name": "Someone Else"})

    assert len(_events(db, p, "renamed")) == 1
    assert _events(db, p, "unpublished") == []


# ── logging a call: POST /api/professionals/{id}/call ───────────────────────
def test_a_call_that_moves_the_stage_takes_it_offline(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    r = as_caller.post(f"/api/professionals/{p.id}/call", json={"stage": "contacted"})
    assert r.status_code == 200, r.text

    assert "moved to contacted" in _taken_down(db, as_caller, p)


def test_a_call_that_renames_takes_it_offline(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    as_caller.post(f"/api/professionals/{p.id}/call",
                   json={"stage": None, "contact_name": "GIORGOS NEOPOULOS"})

    assert "the name changed" in _taken_down(db, as_caller, p)


def test_a_call_that_changes_nothing_approved_keeps_it_live(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    as_caller.post(f"/api/professionals/{p.id}/call",
                   json={"stage": None, "bio": "Called, all good."})

    _still_live(db, as_caller, p)


# ── moving the stage: POST /api/professionals/{id}/stage ────────────────────
@pytest.mark.parametrize("stage", ["imported", "contacted", "details_collected",
                                   "signature_sent", "declined", "not_valid"])
def test_moving_a_live_profile_off_signed_takes_it_offline(as_caller, db, professions, stage):
    p = _live(db, professions, as_caller)

    r = as_caller.post(f"/api/professionals/{p.id}/stage", json={"stage": stage, "note": None})
    assert r.status_code == 200, r.text

    assert f"moved to {stage.replace('_', ' ')}" in _taken_down(db, as_caller, p)


def test_moving_a_live_profile_to_signed_keeps_it_live(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    as_caller.post(f"/api/professionals/{p.id}/stage", json={"stage": "signed", "note": None})

    _still_live(db, as_caller, p)


# ── photo files: upload and delete ──────────────────────────────────────────
def test_uploading_a_new_photo_takes_it_offline(as_caller, db, professions):
    p = _live(db, professions, as_caller)

    r = as_caller.post(f"/api/professionals/{p.id}/photo",
                       files={"file": ("new.png", PNG, "image/png")})
    assert r.status_code == 201, r.text

    assert "the photo changed" in _taken_down(db, as_caller, p)


def test_deleting_the_photo_file_takes_it_offline(as_owner, db, professions):
    p = _live(db, professions, as_owner)
    asset_id = int(p.photo.rsplit("/", 1)[1])

    assert as_owner.delete(f"/api/assets/{asset_id}").status_code == 204

    assert "the photo was removed" in _taken_down(db, as_owner, p)


# ── agreements: issue, and a link still open on a live profile ──────────────
def test_issuing_a_new_agreement_takes_it_offline(as_owner, db, professions):
    p = _live(db, professions, as_owner)

    issued = as_owner.post(f"/api/agreements/issue/{p.id}")
    assert issued.status_code in (200, 201), issued.text

    assert "moved to signature sent" in _taken_down(db, as_owner, p)


def test_signing_an_old_link_on_a_live_profile_takes_it_offline(as_owner, client, db, professions):
    """A link issued before the rule above, still open once the profile went
    live: signing writes the signer's name and photo straight onto it."""
    p = _record(db, professions, as_owner)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    _go_live(db, p)

    r = client.post(f"/api/agreements/{token}/sign",
                    json={**SIGN_BODY, "signed_name": "Demo Realty"})
    assert r.status_code in (200, 201), r.text

    assert "the name changed" in _taken_down(db, client, p)


def test_a_photo_sent_through_an_old_link_takes_it_offline(as_owner, client, db, professions):
    p = _record(db, professions, as_owner)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    _go_live(db, p)

    r = client.post(f"/api/agreements/{token}/photo",
                    files={"file": ("me.png", PNG, "image/png")})
    assert r.status_code == 201, r.text

    assert "the photo changed" in _taken_down(db, client, p)


# ── the profession's own settings: PATCH /api/professions/{id} ──────────────
def test_switching_photos_off_for_a_trade_takes_its_live_profiles_offline(
    as_owner, db, professions,
):
    inherits = _live(db, professions, as_owner)
    own_list = _live(db, professions, as_owner, contact_name="Maria K",
                     visible_fields=sorted(DEFAULT_VISIBLE))

    r = as_owner.patch(f"/api/professions/{professions['agent']}",
                       json={"visible_fields": WITHOUT_PHOTO})
    assert r.status_code == 200, r.text

    assert "the photo was switched off" in _taken_down(db, as_owner, inherits)
    _still_live(db, as_owner, own_list)   # its own list still shows the photo


# ── the gate: what it takes to go back up ───────────────────────────────────
def test_a_signature_covers_only_the_name_it_was_signed_under(as_owner, db, professions):
    p = _record(db, professions, as_owner, contact_name="GIORGOS NEOPOULOS",
                stage=Stage.signed)
    _complete(db, p, "Demo Realty")

    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 409
    assert "not the name the agreement was signed under (Demo Realty)" in r.text

    _complete(db, p, "Giorgos Neopoulos")   # his own, capitals aside
    assert as_owner.post(f"/api/professionals/{p.id}/publish").status_code == 200


def test_the_name_check_ignores_greek_accents_and_capitals(as_owner, db, professions):
    p = _record(db, professions, as_owner, contact_name="ΓΙΩΡΓΟΣ ΝΕΟΠΟΥΛΟΣ",
                stage=Stage.signed)
    _complete(db, p, "Γιώργος Νεόπουλος")

    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 200, r.text


def test_publishing_needs_the_stage_at_signed(as_owner, db, professions):
    p = _record(db, professions, as_owner, stage=Stage.signature_sent)
    _complete(db, p, p.contact_name)

    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 409
    assert "They are at signature sent, not Signed" in r.text


def test_publishing_needs_the_photo_switched_on(as_owner, db, professions):
    p = _record(db, professions, as_owner, stage=Stage.signed,
                visible_fields=WITHOUT_PHOTO)
    _complete(db, p, p.contact_name)

    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 409
    assert "The photo is switched off" in r.text


# ── the whole of 11 November, replayed ──────────────────────────────────────
def test_the_eleventh_of_november_cannot_happen_again(client, owner, caller, db, professions, codes):
    p = Professional(business_name="Harbour Real Estate - Chania",
                     city="Chania", region="Crete", email="office@example.com",
                     profession_id=professions["agent"], stage=Stage.imported)
    db.add(p)
    db.commit()

    # 30 August: a test signing under a made-up name, with a photo, and live.
    _login(client, owner.email, "owner-pw")
    token = client.post(f"/api/agreements/issue/{p.id}").json()["token"]
    photo = client.post(f"/api/agreements/{token}/photo",
                        files={"file": ("test-photo.jpg", PNG, "image/png")}).json()["id"]
    client.post(f"/api/agreements/{token}/sign", json={
        **SIGN_BODY, "signed_name": "Demo Realty", "photo": f"/api/assets/{photo}"})
    assert client.post(f"/api/agreements/{token}/verify",
                       json={"code": codes[-1]}).status_code == 200
    published = client.post(f"/api/professionals/{p.id}/publish")
    assert published.status_code == 200, published.text
    assert published.json()["slug"] == "demo-realty"

    # 11 November: a caller reaches the real agent and types him in.
    _login(client, caller.email, "caller-pw")
    r = client.post(f"/api/professionals/{p.id}/call", json={
        "stage": "contacted", "contact_name": "GIORGOS NEOPOULOS",
        "bio": "30 XRONIA STA CHANIA"})
    assert r.status_code == 200, r.text

    # He never reaches the site, and the history says who and why.
    assert client.get("/api/public/professionals/demo-realty").status_code == 404
    db.expire_all()
    down = db.scalars(select(Event).where(Event.professional_id == p.id,
                                          Event.kind == "unpublished")).one()
    assert down.actor_label == "Caller"
    assert "the name changed from Demo Realty to GIORGOS NEOPOULOS" in down.detail

    # And the test signature cannot put him back up, even forced to Signed.
    _login(client, owner.email, "owner-pw")
    client.post(f"/api/professionals/{p.id}/stage", json={"stage": "signed", "note": None})
    again = client.post(f"/api/professionals/{p.id}/publish")
    assert again.status_code == 409
    assert "signed under (Demo Realty)" in again.text
