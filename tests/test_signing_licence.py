"""The licence a professional attaches while signing (client change requests,
5 October, item 11).

Optional, and private. It is stored as a document — owner-only to open, never
on a public page — so the team can check it before switching on the
"Licensed" badge. The client chose the badge alone over a public "View
licence" link: a licence can carry an ID number and a photograph.

The same change made every upload prove its type from its bytes, since this
one is open to anyone holding a signing link.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models import Asset, AssetKind, Professional, Stage

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 128
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 128
PDF = b"%PDF-1.7\n" + b"0" * 128


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


def _attach(client, token, name, data, content_type):
    return client.post(f"/api/agreements/{token}/licence",
                       files={"file": (name, data, content_type)})


@pytest.mark.parametrize("name, data, declared", [
    ("licence.jpg", JPEG, "image/jpeg"),
    ("licence.png", PNG, "image/png"),
    ("licence.pdf", PDF, "application/pdf"),
])
def test_a_licence_can_be_attached_while_signing(
    as_owner, client, db, professions, name, data, declared,
):
    p = _pro(db, professions)
    r = _attach(client, _token(as_owner, p.id), name, data, declared)
    assert r.status_code == 201, r.text
    asset = db.get(Asset, r.json()["id"])
    assert (asset.kind, asset.field_key, asset.content_type) == (
        AssetKind.document, "licence", declared)
    # It is not the profile photo, and nothing about the record changes.
    db.refresh(p)
    assert p.photo is None


def test_the_licence_is_private(as_owner, client, db, professions):
    p = _pro(db, professions)
    asset_id = _attach(client, _token(as_owner, p.id), "licence.pdf", PDF,
                       "application/pdf").json()["id"]
    client.cookies.clear()
    assert client.get(f"/api/assets/{asset_id}").status_code == 404


def test_only_the_owner_opens_it(as_owner, client, db, professions):
    p = _pro(db, professions)
    asset_id = _attach(client, _token(as_owner, p.id), "licence.pdf", PDF,
                       "application/pdf").json()["id"]
    opened = as_owner.get(f"/api/assets/{asset_id}")
    assert opened.status_code == 200
    assert opened.headers["cache-control"] == "private, no-store"


def test_a_caller_cannot_open_it(as_owner, client, db, professions, caller):
    p = _pro(db, professions)
    asset_id = _attach(client, _token(as_owner, p.id), "licence.pdf", PDF,
                       "application/pdf").json()["id"]
    client.post("/api/auth/logout")
    signed_in = client.post("/api/auth/login",
                            json={"email": caller.email, "password": "caller-pw"})
    assert signed_in.status_code == 200
    assert client.get(f"/api/assets/{asset_id}").status_code == 404


def test_the_team_sees_the_latest_licence_on_the_record(as_owner, db, professions):
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    _attach(as_owner, token, "old.jpg", JPEG, "image/jpeg")
    latest = _attach(as_owner, token, "new.pdf", PDF, "application/pdf").json()
    record = as_owner.get(f"/api/professionals/{p.id}").json()
    assert record["licence_document"]["id"] == latest["id"]
    assert record["licence_document"]["original_filename"] == "new.pdf"


def test_the_signing_page_knows_one_is_on_file(as_owner, client, db, professions):
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    assert client.get(f"/api/agreements/{token}").json()["professional"]["licence_filename"] is None
    _attach(client, token, "my licence.pdf", PDF, "application/pdf")
    page = client.get(f"/api/agreements/{token}").json()
    assert page["professional"]["licence_filename"] == "my licence.pdf"


@pytest.mark.parametrize("name, data, declared", [
    ("licence.pdf", b"<html><script>alert(1)</script></html>", "application/pdf"),
    ("licence.jpg", b"GIF89a" + b"0" * 64, "image/jpeg"),
    ("licence.txt", b"hello", "text/plain"),
    ("licence.pdf", b"", "application/pdf"),
])
def test_a_file_that_is_not_an_image_or_pdf_is_refused(
    as_owner, client, db, professions, name, data, declared,
):
    p = _pro(db, professions)
    r = _attach(client, _token(as_owner, p.id), name, data, declared)
    assert r.status_code == 422, r.text
    assert db.query(Asset).count() == 0


def test_a_used_link_cannot_upload(as_owner, client, db, professions):
    from app.models import Agreement

    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    agreement = db.query(Agreement).filter_by(professional_id=p.id).one()
    agreement.signed_at = datetime.now(timezone.utc)
    db.commit()
    assert _attach(client, token, "licence.pdf", PDF, "application/pdf").status_code == 410


def test_the_public_profile_never_carries_it(as_owner, client, db, professions):
    p = _pro(db, professions, slug="kostas-p", published=True, stage=Stage.signed,
             published_at=datetime.now(timezone.utc))
    asset_id = _attach(client, _token(as_owner, p.id), "licence.pdf", PDF,
                       "application/pdf").json()["id"]
    profile = client.get("/api/public/professionals/kostas-p")
    assert profile.status_code == 200
    assert f"/api/assets/{asset_id}" not in profile.text
    assert "licence.pdf" not in profile.text


def test_a_photo_is_stored_as_what_it_is(as_owner, client, db, professions):
    # A PNG saved with a .jpg name arrives declared as image/jpeg. It is kept,
    # as the PNG it is, rather than refused or mislabelled.
    p = _pro(db, professions)
    r = client.post(f"/api/agreements/{_token(as_owner, p.id)}/photo",
                    files={"file": ("me.jpg", PNG, "image/jpeg")})
    assert r.status_code == 201, r.text
    assert db.get(Asset, r.json()["id"]).content_type == "image/png"


def test_one_link_takes_at_most_ten_files(as_owner, client, db, professions):
    # Photos and licences together: each may be 20MB, and anyone holding an
    # unsigned link could otherwise keep sending them until it expires.
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    for i in range(10):
        route = "photo" if i % 2 else "licence"
        r = client.post(f"/api/agreements/{token}/{route}",
                        files={"file": (f"file{i}.jpg", JPEG, "image/jpeg")})
        assert r.status_code == 201, (i, r.text)
    r = _attach(client, token, "one-more.pdf", PDF, "application/pdf")
    assert r.status_code == 429
    assert "a lot of uploads" in r.json()["detail"]


def test_the_team_uploading_does_not_use_up_a_link(as_owner, db, professions):
    p = _pro(db, professions)
    token = _token(as_owner, p.id)
    for i in range(10):
        r = as_owner.post(f"/api/professionals/{p.id}/photo",
                          files={"file": (f"staff{i}.jpg", JPEG, "image/jpeg")})
        assert r.status_code == 201, r.text
    assert _attach(as_owner, token, "licence.pdf", PDF, "application/pdf").status_code == 201
