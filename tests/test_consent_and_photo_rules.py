"""Three rules an agent review found unguarded, all about the same thing:
what counts as proven, and what a public form is allowed to write.

  * **The publish gate read the wrong agreement.** It asked "is the most recent
    agreement confirmed" instead of "does a confirmed agreement exist". An
    owner re-issuing a link — for any reason — over a completed agreement, and
    the professional not finishing the second one, locked the profile out of
    publishing despite valid consent already on file.

  * **Signing overwrote the record's email before proving it.** The address
    typed into a public form replaced the one a caller had confirmed by phone,
    and was then where the confirmation code was sent. Get it wrong and there
    is no way back: resend reuses the bad address and re-signing is a spent
    link.

  * **The staff edit path wrote arbitrary photo strings.** The signing path was
    hardened when uploads moved to object storage; `PATCH` was not, and the
    call form exposes `photo` as a free text field.
"""
from __future__ import annotations

import re

import pytest

from app.models import Agreement, Asset, AssetKind, Professional, Stage

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 128

SIGN = {
    "signed_name": "Kostas Papadopoulos",
    "signature_image": "<svg/>",
    "licence": "ABC123",
    "vat_number": "EL123456789",
    "email": "kostas@example.com",
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


def _pro(db, professions, **kw):
    base = dict(business_name="Papadopoulos & Partners", contact_name="Kostas P",
                city="Chania", region="Crete", email="onfile@example.com",
                phone="+30 111", profession_id=professions["lawyer"],
                stage=Stage.details_collected)
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _token(as_owner, professional_id: int) -> str:
    r = as_owner.post(f"/api/agreements/issue/{professional_id}")
    assert r.status_code in (200, 201), r.text
    return r.json()["token"]


def _complete(as_owner, client, db, professional_id, codes, **overrides):
    """Sign and confirm — a fully completed agreement."""
    token = _token(as_owner, professional_id)
    r = client.post(f"/api/agreements/{token}/sign", json={**SIGN, **overrides})
    assert r.status_code in (200, 201), r.text
    v = client.post(f"/api/agreements/{token}/verify", json={"code": codes[-1]})
    assert v.status_code == 200, v.text
    return token


# ── the publish gate ────────────────────────────────────────────────────────
def test_a_second_abandoned_agreement_does_not_shadow_a_completed_one(
    as_owner, client, db, professions, codes,
):
    """The bug: consent was on file and the profile could never be published."""
    p = _pro(db, professions)
    _complete(as_owner, client, db, p.id, codes)

    ready = as_owner.get(f"/api/professionals/{p.id}/readiness").json()
    assert ready["ready"] is False or "photo" in str(ready), "signed and confirmed"

    # The owner issues another link; the professional signs but never confirms.
    second = _token(as_owner, p.id)
    assert client.post(f"/api/agreements/{second}/sign", json=SIGN).status_code in (200, 201)

    blockers = as_owner.get(f"/api/professionals/{p.id}/readiness").json()["blockers"]
    assert not any("email" in b.lower() or "confirm" in b.lower() for b in blockers), (
        f"a completed agreement is on file, but the gate reports {blockers} — "
        f"it is reading the latest agreement instead of asking whether a "
        f"confirmed one exists"
    )


def test_an_abandoned_agreement_alone_still_blocks_publishing(
    as_owner, client, db, professions,
):
    """The gate must not be loosened into uselessness: signing without
    confirming is exactly the state it exists to catch."""
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    client.post(f"/api/agreements/{token}/sign", json=SIGN)

    blockers = as_owner.get(f"/api/professionals/{p.id}/readiness").json()["blockers"]
    assert any("email" in b.lower() or "confirm" in b.lower() for b in blockers), blockers


# ── the signed email ────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad", [
    "", "not-an-email", "a@x.com, b@y.com", "a@x.com,b@y.com",
    "kostas@example.com\nBcc: attacker@evil.com",
])
def test_signing_refuses_an_address_we_could_not_write_to(bad, as_owner, client, db, professions):
    p = _pro(db, professions)
    r = client.post(f"/api/agreements/{_token(as_owner, p.id)}/sign",
                    json={**SIGN, "email": bad})
    assert r.status_code == 422, f"{bad!r} was accepted as a contact address"

    db.expire_all()
    assert db.get(Professional, p.id).email == "onfile@example.com", (
        "the caller-confirmed address was overwritten by a rejected one"
    )


def test_the_record_keeps_its_address_until_the_code_comes_back(
    as_owner, client, db, professions, codes,
):
    """Signing is a claim; confirming is the proof. Until then the address a
    caller confirmed by phone is the only one known to reach this person."""
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    client.post(f"/api/agreements/{token}/sign", json={**SIGN, "email": "new@example.com"})

    db.expire_all()
    assert db.get(Professional, p.id).email == "onfile@example.com"

    assert client.post(f"/api/agreements/{token}/verify",
                       json={"code": codes[-1]}).status_code == 200
    db.expire_all()
    assert db.get(Professional, p.id).email == "new@example.com"


def test_an_over_long_field_is_a_clear_refusal_not_a_500(as_owner, client, db, professions):
    """Cross-origin, an opaque 500 reaches the signer as "check your
    connection" — on a form they have just spent minutes on."""
    p = _pro(db, professions)
    r = client.post(f"/api/agreements/{_token(as_owner, p.id)}/sign",
                    json={**SIGN, "phone": "+30" + "1" * 500})
    assert r.status_code == 422, r.text


# ── the photo column ────────────────────────────────────────────────────────
@pytest.mark.parametrize("value", [
    "https://evil.example.com/tracker.jpg",
    "data:image/jpeg;base64," + "A" * 400,
    "/api/assets/999999",
    "//evil.example.com/x.jpg",
])
def test_a_caller_cannot_put_an_arbitrary_photo_on_a_public_profile(
    value, as_caller, db, professions,
):
    """The call form exposes `photo` as free text. Whatever is typed there was
    served from a page carrying Vilaow's name."""
    p = _pro(db, professions)
    r = as_caller.patch(f"/api/professionals/{p.id}", json={"photo": value})
    assert r.status_code == 422, f"{value!r} was accepted onto a public profile"

    db.expire_all()
    assert db.get(Professional, p.id).photo is None


def test_a_caller_cannot_publish_another_professionals_photo(
    as_caller, db, professions,
):
    mine = _pro(db, professions)
    theirs = _pro(db, professions, business_name="Other firm")
    uploaded = as_caller.post(f"/api/professionals/{theirs.id}/photo",
                              files={"file": ("them.jpg", JPEG, "image/jpeg")}).json()["id"]

    r = as_caller.patch(f"/api/professionals/{mine.id}",
                        json={"photo": f"/api/assets/{uploaded}"})
    assert r.status_code == 422, r.text


def test_a_licence_scan_cannot_be_made_the_public_photo(as_caller, db, professions):
    """Documents are owner-only. Setting one as the photo would publish it."""
    from app.models import FieldType, ProfessionField

    p = _pro(db, professions)
    db.add(ProfessionField(profession_id=p.profession_id, key="licence_scan",
                           label="Licence", type=FieldType.file,
                           required=False, public=False))
    db.commit()
    doc = as_caller.post(f"/api/professionals/{p.id}/files/licence_scan",
                         files={"file": ("l.pdf", b"%PDF-1.4 scan", "application/pdf")}).json()
    assert db.get(Asset, doc["id"]).kind is AssetKind.document

    r = as_caller.patch(f"/api/professionals/{p.id}", json={"photo": f"/api/assets/{doc['id']}"})
    assert r.status_code == 422, "a licence scan was accepted as a public photo"


def test_an_uploaded_photo_is_still_accepted(as_caller, db, professions):
    """The rule must not block the only legitimate path."""
    p = _pro(db, professions)
    asset = as_caller.post(f"/api/professionals/{p.id}/photo",
                           files={"file": ("me.jpg", JPEG, "image/jpeg")}).json()
    r = as_caller.patch(f"/api/professionals/{p.id}", json={"photo": f"/api/assets/{asset['id']}"})
    assert r.status_code == 200, r.text


def test_clearing_the_photo_is_still_allowed(as_caller, db, professions):
    """That is how a photo is removed from a profile."""
    p = _pro(db, professions)
    as_caller.post(f"/api/professionals/{p.id}/photo",
                   files={"file": ("me.jpg", JPEG, "image/jpeg")})
    assert as_caller.patch(f"/api/professionals/{p.id}", json={"photo": None}).status_code == 200
    db.expire_all()
    assert db.get(Professional, p.id).photo is None
