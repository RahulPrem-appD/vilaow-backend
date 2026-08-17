"""The photo a professional attaches while signing.

There used to be two photo paths that did not meet. A caller's upload went
through `/api/professionals/{id}/photo` into object storage, and the column
held `/api/assets/N`. A photo attached while *signing* was posted as a base64
`data:` URL and written onto the same column verbatim — several hundred
kilobytes of text in a row that every list response selects, fifty at a time.

Fixing it closed a hole nobody had noticed. That field was public input written
straight onto a public profile, so a token holder could have put an arbitrary
external URL there, and Vilaow would have served it from a page carrying its
own name. The photo now has to be an asset we stored, for this professional.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models import Agreement, Asset, AssetKind, Professional, Stage

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 128


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
                city="Heraklion", region="Crete", email="k@example.com",
                profession_id=professions["lawyer"], stage=Stage.details_collected)
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _token(as_owner, professional_id: int) -> str:
    issued = as_owner.post(f"/api/agreements/issue/{professional_id}")
    assert issued.status_code in (200, 201), issued.text
    return issued.json()["token"]


SIGN = {
    "signed_name": "Kostas Papadopoulos",
    "signature_image": "data:image/png;base64,iVBORw0KGgo=",
    "licence": "ABC123",
    "vat_number": "EL123456789",
    "email": "k@example.com",
    "phone": "+30 210 111 1111",
    "agreed": True,
}


def test_the_signing_photo_goes_to_storage_not_into_the_row(
    as_owner, client, db, professions,
):
    """The whole point. The column holds a reference; the bytes live in the
    bucket, where the caller's uploads already went."""
    p = _pro(db, professions)
    token = _token(as_owner, p.id)

    r = client.post(f"/api/agreements/{token}/photo",
                    files={"file": ("me.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 201, r.text
    path = f"/api/assets/{r.json()['id']}"

    db.refresh(p)
    assert p.photo == path

    signed = client.post(f"/api/agreements/{token}/sign", json={**SIGN, "photo": path})
    assert signed.status_code in (200, 201), signed.text

    db.expire_all()
    stored = db.get(Professional, p.id).photo
    assert stored == path
    assert not stored.startswith("data:"), "the row is holding image bytes again"


def test_the_uploaded_photo_can_be_read_back(as_owner, client, db, professions):
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    asset_id = client.post(f"/api/agreements/{token}/photo",
                           files={"file": ("me.jpg", JPEG, "image/jpeg")}).json()["id"]

    # No session: a profile photo is public, and this is the same route the
    # public profile page renders it from.
    got = client.get(f"/api/assets/{asset_id}")
    assert got.status_code == 200
    assert got.content == JPEG


def test_signing_refuses_a_photo_belonging_to_someone_else(
    as_owner, client, db, professions,
):
    """Otherwise a token holder could point their profile at any stored photo,
    including one uploaded for a different professional."""
    mine = _pro(db, professions)
    theirs = _pro(db, professions, business_name="Other firm", email="o@example.com")

    stolen = client.post(f"/api/agreements/{_token(as_owner, theirs.id)}/photo",
                         files={"file": ("them.jpg", JPEG, "image/jpeg")}).json()["id"]

    r = client.post(f"/api/agreements/{_token(as_owner, mine.id)}/sign",
                    json={**SIGN, "photo": f"/api/assets/{stolen}"})
    assert r.status_code == 422, r.text

    db.expire_all()
    assert db.get(Professional, mine.id).photo is None


@pytest.mark.parametrize("photo", [
    "data:image/jpeg;base64,/9j/4AAQSkZJRg==",
    "https://example.com/not-ours.jpg",
    "/api/assets/999999",
    "/api/assets/1/../../etc/passwd",
])
def test_signing_refuses_a_photo_we_did_not_store(
    photo, as_owner, client, db, professions,
):
    p = _pro(db, professions)
    r = client.post(f"/api/agreements/{_token(as_owner, p.id)}/sign",
                    json={**SIGN, "photo": photo})
    assert r.status_code == 422, f"{photo} was accepted onto a public profile"

    db.expire_all()
    assert db.get(Professional, p.id).photo is None


def test_signing_refuses_a_deleted_photo(as_owner, client, db, professions):
    """Deletion is erasure. A reference to an erased file must not come back
    onto the profile through the signing payload."""
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    asset_id = client.post(f"/api/agreements/{token}/photo",
                           files={"file": ("me.jpg", JPEG, "image/jpeg")}).json()["id"]
    assert as_owner.delete(f"/api/assets/{asset_id}").status_code in (200, 204)

    r = client.post(f"/api/agreements/{token}/sign",
                    json={**SIGN, "photo": f"/api/assets/{asset_id}"})
    assert r.status_code == 422, r.text


def test_a_document_cannot_be_passed_off_as_a_photo(
    as_owner, as_caller, client, db, professions,
):
    """Documents are owner-only and answered with 404 to everyone else. Setting
    one as the profile photo would publish a licence scan."""
    from app.models import FieldType, ProfessionField

    p = _pro(db, professions)
    db.add(ProfessionField(profession_id=p.profession_id, key="licence_scan",
                           label="Licence", type=FieldType.file,
                           required=False, public=False))
    db.commit()

    doc = as_caller.post(f"/api/professionals/{p.id}/files/licence_scan",
                         files={"file": ("licence.pdf", b"%PDF-1.4 scan", "application/pdf")})
    assert doc.status_code == 201, doc.text
    assert db.get(Asset, doc.json()["id"]).kind is AssetKind.document

    r = client.post(f"/api/agreements/{_token(as_owner, p.id)}/sign",
                    json={**SIGN, "photo": f"/api/assets/{doc.json()['id']}"})
    assert r.status_code == 422, "a licence scan was accepted as a public photo"


# ── who may upload ──────────────────────────────────────────────────────────
def test_the_upload_needs_a_token(client, db, professions):
    _pro(db, professions)
    r = client.post("/api/agreements/not-a-real-token/photo",
                    files={"file": ("me.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 404


def test_a_spent_link_cannot_still_upload(as_owner, client, db, professions):
    """The link is one-time for signing; it has to be one-time for uploading
    too, or it stays a live write endpoint after the agreement is closed."""
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    client.post(f"/api/agreements/{token}/photo",
                files={"file": ("me.jpg", JPEG, "image/jpeg")})
    signed = client.post(f"/api/agreements/{token}/sign", json=SIGN)
    assert signed.status_code in (200, 201), signed.text

    r = client.post(f"/api/agreements/{token}/photo",
                    files={"file": ("again.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 410, r.text


def test_an_expired_link_cannot_upload(as_owner, client, db, professions):
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    agreement = db.query(Agreement).filter_by(professional_id=p.id).one()
    agreement.expires_at = datetime.now(UTC) - timedelta(days=1)
    db.commit()

    r = client.post(f"/api/agreements/{token}/photo",
                    files={"file": ("me.jpg", JPEG, "image/jpeg")})
    assert r.status_code == 410, r.text


def test_the_upload_still_refuses_what_the_staff_route_refuses(
    as_owner, client, db, professions,
):
    """It is the same use case with a different credential, so the file rules
    must not have been re-implemented more loosely on the way in."""
    p = _pro(db, professions)
    token = _token(as_owner, p.id)

    r = client.post(f"/api/agreements/{token}/photo",
                    files={"file": ("virus.exe", b"MZ" + b"0" * 64, "application/x-msdownload")})
    assert r.status_code == 422, r.text

    r = client.post(f"/api/agreements/{token}/photo",
                    files={"file": ("huge.jpg", b"\xff\xd8\xff\xe0" + b"0" * (9 * 1024 * 1024),
                                    "image/jpeg")})
    assert r.status_code == 422, r.text


def test_the_signing_page_shows_a_photo_the_caller_already_uploaded(
    as_owner, as_caller, client, db, professions,
):
    """So the form can say "we have this" rather than demanding a second copy
    of a photo that was taken down over the phone."""
    p = _pro(db, professions)
    as_caller.post(f"/api/professionals/{p.id}/photo",
                   files={"file": ("me.jpg", JPEG, "image/jpeg")})

    page = client.get(f"/api/agreements/{_token(as_owner, p.id)}")
    assert page.status_code == 200, page.text
    db.refresh(p)
    assert page.json()["professional"]["photo"] == p.photo


def test_signing_without_a_photo_keeps_the_one_already_there(
    as_owner, as_caller, client, db, professions,
):
    """A blank field means "nothing new", not "erase what the caller
    collected" — the mistake already made once with licence and VAT."""
    p = _pro(db, professions)
    as_caller.post(f"/api/professionals/{p.id}/photo",
                   files={"file": ("me.jpg", JPEG, "image/jpeg")})
    db.refresh(p)
    existing = p.photo

    r = client.post(f"/api/agreements/{_token(as_owner, p.id)}/sign",
                    json={**SIGN, "photo": None})
    assert r.status_code in (200, 201), r.text

    db.expire_all()
    assert db.get(Professional, p.id).photo == existing
