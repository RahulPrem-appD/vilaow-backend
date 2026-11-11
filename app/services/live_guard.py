"""Keeping a live profile the one the owner approved.

The publish gate (app/domain/publishing.py) is checked once, when the owner
presses Publish. Everything written after that used to reach the site
unchecked. On 11 November a caller typed a new person's name and details into
a profile that had been live since a test signing on 30 August: they were
public at once, under someone else's signature, and its photo was gone.

So a live profile now goes offline the moment something the owner approved
changes:

  * the name buyers see — capitals, accents and spacing aside (`name_key`)
  * the photo buyers see — removed, replaced, or switched off
  * the profession
  * the stage, when it moves anywhere but Signed

Offline means unpublished, with the reason in its history. The owner publishes
it again when it is right, and the gate checks the new state then. Every other
edit still shows straight away, which is how a bio or a language list gets
corrected without a trip through the owner.

Each write of those four takes a `snapshot` before it writes and calls
`enforce` after, before its own commit. There are eleven such writes across
four services; tests/test_live_guard.py goes through them one by one.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.domain.publishing import name_key
from app.domain.visibility import resolve
from app.models import Event, Profession, Professional, Stage


@dataclass(frozen=True)
class Shown:
    """What a buyer sees of one record, as far as the owner approved it."""

    name: str
    photo: str | None
    profession_id: int | None
    stage: Stage


def snapshot(db: Session, professional: Professional) -> Shown:
    # By id rather than the relationship, which still points at the old
    # profession until a flush when profession_id has just been changed.
    profession = (db.get(Profession, professional.profession_id)
                  if professional.profession_id is not None else None)
    visible = resolve(professional.visible_fields,
                      profession.visible_fields if profession else None)
    return Shown(
        name=(professional.contact_name or professional.business_name or "").strip(),
        photo=(professional.photo or None) if "photo" in visible else None,
        profession_id=professional.profession_id,
        stage=professional.stage,
    )


def _changes(before: Shown, after: Shown, professional: Professional) -> list[str]:
    changes: list[str] = []
    if after.stage != before.stage and after.stage is not Stage.signed:
        changes.append(f"moved to {after.stage.value.replace('_', ' ')}")
    if name_key(after.name) != name_key(before.name):
        changes.append(
            f"the name changed from {before.name or 'nothing'} to {after.name or 'nothing'}"
        )
    if after.photo != before.photo:
        if after.photo is not None:
            changes.append("the photo changed")
        elif professional.photo:
            changes.append("the photo was switched off")
        else:
            changes.append("the photo was removed")
    if after.profession_id != before.profession_id:
        changes.append("the profession changed")
    return changes


def enforce(db: Session, professional: Professional, before: Shown, *,
            actor_id: int | None, actor_label: str | None) -> str | None:
    """Takes `professional` offline if a write since `before` changed what was
    approved, and says why in its history.

    Adds the event and leaves the commit to the caller, which is committing
    its own write anyway. Returns the reason, or None when the profile was not
    live or nothing approved changed.
    """
    if not professional.published:
        return None
    changes = _changes(before, snapshot(db, professional), professional)
    if not changes:
        return None
    reason = "; ".join(changes)
    professional.published = False
    professional.published_at = None
    db.add(Event(
        professional_id=professional.id,
        actor_id=actor_id,
        actor_label=actor_label,
        kind="unpublished",
        detail=f"Taken off the site: {reason}",
    ))
    return reason
