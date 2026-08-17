"""Uploads, and who is allowed to read them back.

A photo goes on a public profile. A licence scan is an identity document that
happens to sit in the same table. The tests below exist because those two must
never be confused: most of them are attempts to read a document as somebody who
should not be able to.
"""
from __future__ import annotations

import pytest

from app.models import Asset, FieldType, Professional, ProfessionField


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture(autouse=True)
def uploads_in_tmp(tmp_path):
    """Never write into the repo while testing.

    Another port paying off: storage is injected, so the suite hands the app a
    LocalStorage rooted in a temp directory instead of reaching in to swap a
    module-level singleton.
    """
    from app.adapters.storage.local import LocalStorage
    from app.api.deps import get_storage
    from app.main import app

    app.dependency_overrides[get_storage] = lambda: LocalStorage(tmp_path)
    yield
    app.dependency_overrides.pop(get_storage, None)


def _pro(db, professions, **kw):
    base = dict(business_name="Papadopoulos & Partners", contact_name="Kostas P",
                city="Heraklion", region="Crete", profession_id=professions["lawyer"])
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _file_field(db, profession_id, key="insurance_doc"):
    f = ProfessionField(profession_id=profession_id, key=key, label="Insurance",
                        type=FieldType.file, required=False, public=False)
    db.add(f)
    db.commit()
    return f


# ── photos ──────────────────────────────────────────────────────────────────
def test_a_caller_can_upload_a_photo_and_anyone_can_see_it(as_caller, client, db, professions):
    p = _pro(db, professions)
    r = as_caller.post(f"/api/professionals/{p.id}/photo",
                       files={"file": ("me.png", PNG, "image/png")})
    assert r.status_code == 201, r.text

    db.refresh(p)
    assert p.photo == f"/api/assets/{r.json()['id']}"

    # A stranger has to be able to load it — it is on a public profile.
    got = client.get(p.photo)
    assert got.status_code == 200
    assert got.content == PNG


def test_the_browsers_filename_is_never_used_as_the_key(as_caller, db, professions):
    """An upload filename is attacker-controlled: separators, null bytes, a
    second extension. The stored key is generated instead."""
    p = _pro(db, professions)
    r = as_caller.post(f"/api/professionals/{p.id}/photo",
                       files={"file": ("../../../etc/passwd.png", PNG, "image/png")})
    assert r.status_code == 201

    asset = db.get(Asset, r.json()["id"])
    assert ".." not in asset.storage_path
    assert "etc/passwd" not in asset.storage_path
    # Kept for display, just not trusted as a path.
    assert asset.original_filename == "../../../etc/passwd.png"


def test_an_executable_is_refused(as_caller, db, professions):
    p = _pro(db, professions)
    r = as_caller.post(f"/api/professionals/{p.id}/photo",
                       files={"file": ("x.exe", b"MZ", "application/x-msdownload")})
    assert r.status_code == 422


# ── documents ───────────────────────────────────────────────────────────────
def test_a_document_is_invisible_to_a_stranger(as_caller, client, db, professions):
    p = _pro(db, professions)
    _file_field(db, professions["lawyer"])
    r = as_caller.post(f"/api/professionals/{p.id}/files/insurance_doc",
                       files={"file": ("licence.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 201, r.text

    # 404, not 403 — probing ids must not reveal which documents exist.
    assert client.get(f"/api/assets/{r.json()['id']}").status_code == 404


def test_a_caller_cannot_download_a_document_they_uploaded(as_caller, db, professions):
    """Owner-only means owner-only. A caller can see that a licence is
    attached; they cannot pull the file."""
    p = _pro(db, professions)
    _file_field(db, professions["lawyer"])
    asset_id = as_caller.post(f"/api/professionals/{p.id}/files/insurance_doc",
                              files={"file": ("l.pdf", b"%PDF-1.4", "application/pdf")}).json()["id"]

    assert as_caller.get(f"/api/assets/{asset_id}").status_code == 404


def test_the_owner_can_download_a_document(as_owner, db, professions):
    p = _pro(db, professions)
    _file_field(db, professions["lawyer"])
    asset_id = as_owner.post(f"/api/professionals/{p.id}/files/insurance_doc",
                             files={"file": ("l.pdf", b"%PDF-1.4", "application/pdf")}).json()["id"]

    got = as_owner.get(f"/api/assets/{asset_id}")
    assert got.status_code == 200
    assert got.content == b"%PDF-1.4"
    # And never in a shared cache.
    assert "no-store" in got.headers["cache-control"]


def test_a_document_answers_its_field(as_caller, db, professions):
    p = _pro(db, professions)
    _file_field(db, professions["lawyer"])
    asset_id = as_caller.post(f"/api/professionals/{p.id}/files/insurance_doc",
                              files={"file": ("l.pdf", b"%PDF-1.4", "application/pdf")}).json()["id"]

    db.refresh(p)
    assert p.custom["insurance_doc"] == asset_id


def test_a_file_cannot_be_attached_to_a_field_that_is_not_a_file(as_caller, db, professions):
    p = _pro(db, professions)
    db.add(ProfessionField(profession_id=professions["lawyer"], key="bar_number",
                           label="Bar number", type=FieldType.short_text))
    db.commit()

    r = as_caller.post(f"/api/professionals/{p.id}/files/bar_number",
                       files={"file": ("l.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 422


def test_an_unknown_field_is_not_found(as_caller, db, professions):
    p = _pro(db, professions)
    r = as_caller.post(f"/api/professionals/{p.id}/files/nope",
                       files={"file": ("l.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 404


# ── erasure ─────────────────────────────────────────────────────────────────
def test_deleting_a_document_clears_the_answer_too(as_owner, db, professions):
    """Retention is manual deletion by design, so deletion has to actually
    finish the job rather than leaving a dangling id behind."""
    p = _pro(db, professions)
    _file_field(db, professions["lawyer"])
    asset_id = as_owner.post(f"/api/professionals/{p.id}/files/insurance_doc",
                             files={"file": ("l.pdf", b"%PDF-1.4", "application/pdf")}).json()["id"]

    assert as_owner.delete(f"/api/assets/{asset_id}").status_code == 204

    db.refresh(p)
    assert "insurance_doc" not in (p.custom or {})
    assert as_owner.get(f"/api/assets/{asset_id}").status_code == 404


def test_deleting_a_photo_clears_it_from_the_profile(as_owner, db, professions):
    p = _pro(db, professions)
    asset_id = as_owner.post(f"/api/professionals/{p.id}/photo",
                             files={"file": ("me.png", PNG, "image/png")}).json()["id"]

    as_owner.delete(f"/api/assets/{asset_id}")
    db.refresh(p)
    assert p.photo is None


def test_a_caller_cannot_delete(as_caller, db, professions):
    p = _pro(db, professions)
    asset_id = as_caller.post(f"/api/professionals/{p.id}/photo",
                              files={"file": ("me.png", PNG, "image/png")}).json()["id"]
    assert as_caller.delete(f"/api/assets/{asset_id}").status_code == 403
