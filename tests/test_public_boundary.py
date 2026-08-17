"""The boundary that decides what a stranger sees, at its edges.

`public_values()` works from field *definitions* rather than stored data, and
that direction is the whole safety property: a field decides whether its answer
is public, so an answer cannot carry its own permission around with it.

The straightforward cases were covered. These are the edges an agent review
found unpinned — every one is a way an answer given in confidence could reach a
public profile without anybody editing it:

  * a field switched from public to internal, or deactivated, while an answer
    for it is already stored
  * a key deleted and later reused by a different field with different rules
  * a professional moved to another profession whose form happens to use the
    same key
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.fields import public_values
from app.models import FieldType, Professional, ProfessionField, Stage

SECRET = "Discounts for repeat clients — do not publish"


def _field(db, profession_id, key, **kw):
    base = dict(profession_id=profession_id, key=key, label=key.replace("_", " ").title(),
                type=FieldType.short_text, required=False, public=True, active=True,
                position=0)
    base.update(kw)
    f = ProfessionField(**base)
    db.add(f)
    db.commit()
    return f


def _published(db, professions, **kw):
    base = dict(business_name="Papadopoulos & Partners", contact_name="Kostas P",
                email="k@example.com", city="Chania", region="Crete",
                profession_id=professions["lawyer"], slug="kostas",
                published=True, stage=Stage.signed,
                published_at=datetime.now(timezone.utc))
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _fields(db, profession_id):
    from app.repositories.publishing import profession_fields
    return profession_fields(db, profession_id)


def public_keys(db, professional) -> set[str]:
    return {v["key"] for v in
            public_values(_fields(db, professional.profession_id), professional.custom)}


# ── a field that stops being public ─────────────────────────────────────────
def test_making_a_field_internal_hides_an_answer_already_stored(db, professions, client):
    """The answer does not move; only the definition changes. If the boundary
    read stored data instead of definitions, it would still be published."""
    field = _field(db, professions["lawyer"], "fees")
    p = _published(db, professions, custom={"fees": SECRET})
    assert public_keys(db, p) == {"fees"}

    field.public = False
    db.commit()
    db.expire_all()

    p = db.get(Professional, p.id)
    assert public_keys(db, p) == set()
    assert SECRET not in client.get(f"/api/public/professionals/{p.slug}").text


def test_deactivating_a_field_hides_an_answer_already_stored(db, professions, client):
    field = _field(db, professions["lawyer"], "fees")
    p = _published(db, professions, custom={"fees": SECRET})

    field.active = False
    db.commit()
    db.expire_all()

    p = db.get(Professional, p.id)
    assert public_keys(db, p) == set()
    assert SECRET not in client.get(f"/api/public/professionals/{p.slug}").text


def test_an_answer_with_no_field_at_all_is_never_published(db, professions, client):
    """Orphan keys — left by a deleted field, or written before one existed —
    have no definition to say they may be shown, so they may not be."""
    _field(db, professions["lawyer"], "languages_spoken")
    p = _published(db, professions,
                   custom={"languages_spoken": "English", "old_internal_note": SECRET})

    assert public_keys(db, p) == {"languages_spoken"}
    assert SECRET not in client.get(f"/api/public/professionals/{p.slug}").text


# ── a key reused by a different field ───────────────────────────────────────
def test_a_deleted_key_reused_by_an_internal_field_stays_hidden(db, professions, client):
    """The dangerous direction: an answer stored while the key was public,
    still sitting there when a new field claims the key privately."""
    old = _field(db, professions["lawyer"], "fees", public=True)
    p = _published(db, professions, custom={"fees": SECRET})

    db.delete(old)
    db.commit()
    _field(db, professions["lawyer"], "fees", public=False, label="Fees (internal)")
    db.expire_all()

    p = db.get(Professional, p.id)
    assert public_keys(db, p) == set(), "a stale answer was published under a private field"
    assert SECRET not in client.get(f"/api/public/professionals/{p.slug}").text


def test_a_reused_key_takes_the_new_fields_label(db, professions):
    """The definition wins on presentation too, not just on visibility."""
    old = _field(db, professions["lawyer"], "fees", label="Old label")
    p = _published(db, professions, custom={"fees": "€450"})
    db.delete(old)
    db.commit()
    _field(db, professions["lawyer"], "fees", label="Typical fee", public=True)
    db.expire_all()

    p = db.get(Professional, p.id)
    shown = public_values(_fields(db, p.profession_id), p.custom)
    assert [v["label"] for v in shown] == ["Typical fee"]


# ── moving to another profession ────────────────────────────────────────────
def test_changing_profession_does_not_republish_the_old_answers(
    as_owner, db, professions, client,
):
    """Two forms, one key, different rules. "fees" is internal for a lawyer and
    public for an estate agent, so carrying the answer across would publish
    something given in confidence — with nobody editing the answer at all."""
    _field(db, professions["lawyer"], "fees", public=False)
    _field(db, professions["agent"], "fees", public=True)

    p = _published(db, professions, custom={"fees": SECRET})
    assert public_keys(db, p) == set()

    r = as_owner.patch(f"/api/professionals/{p.id}",
                       json={"profession_id": professions["agent"]})
    assert r.status_code == 200, r.text
    db.expire_all()

    p = db.get(Professional, p.id)
    assert public_keys(db, p) == set(), (
        "an answer given under one profession's private field was republished "
        "under another profession's public one"
    )
    assert SECRET not in client.get(f"/api/public/professionals/{p.slug}").text


def test_staying_on_the_same_profession_keeps_the_answers(as_owner, db, professions):
    """The clearing must not fire on an ordinary edit that happens to include
    the profession it already has."""
    _field(db, professions["lawyer"], "fees", public=True)
    p = _published(db, professions, custom={"fees": "€450"})

    assert as_owner.patch(f"/api/professionals/{p.id}",
                          json={"profession_id": professions["lawyer"],
                                "city": "Heraklion"}).status_code == 200
    db.expire_all()
    assert db.get(Professional, p.id).custom == {"fees": "€450"}


# ── file fields are never public, whatever the definition says ──────────────
def test_a_file_field_marked_public_still_publishes_nothing(db, professions, client):
    """Belt and braces: a licence scan is owner-only, and marking its field
    public — by mistake or otherwise — must not be enough to expose it."""
    _field(db, professions["lawyer"], "licence_scan", type=FieldType.file, public=True)
    p = _published(db, professions, custom={"licence_scan": 7})

    assert public_keys(db, p) == set()
    assert "licence_scan" not in client.get(f"/api/public/professionals/{p.slug}").text


@pytest.mark.parametrize("blank", [None, "", "   ", [], {}])
def test_a_blank_answer_is_not_published_as_an_empty_row(blank, db, professions):
    _field(db, professions["lawyer"], "fees")
    p = _published(db, professions, custom={"fees": blank})
    assert public_keys(db, p) == set()


def test_the_boundary_can_actually_fail():
    """A guard that cannot fail is not a guard. If `public_values` ever read
    stored data instead of definitions, this is the shape that would pass."""
    from app.domain.fields import public_values as under_test

    class _Field:
        def __init__(self, key, public, active=True, type=FieldType.short_text):
            self.key, self.public, self.active, self.type = key, public, active, type
            self.label, self.position = key, 0

    assert under_test([_Field("fees", public=True)], {"fees": "x"})
    assert not under_test([_Field("fees", public=False)], {"fees": "x"})
    assert not under_test([_Field("fees", public=True, active=False)], {"fees": "x"})
    assert not under_test([], {"fees": "x"})
