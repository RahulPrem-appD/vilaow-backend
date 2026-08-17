"""Professions, and the form the owner builds for each one.

Reading is staff-wide — a caller cannot fill in a form they cannot see — and
every write is owner-only. That split is enforced in the router, because it is
about who is asking rather than about the rule itself; what lives here is what
makes a field definition valid.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.errors import Conflict, Invalid, NotFound
from app.models import FieldType, Profession, ProfessionField

# Only these two are answered by picking from a list, so only these two need
# one. A select with no options is a field nobody can ever fill in.
CHOICE_TYPES = {FieldType.select, FieldType.multi_select}


class ProfessionService:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ── professions ─────────────────────────────────────────────────────────
    def list(self) -> list[Profession]:
        return list(self._db.scalars(
            select(Profession).order_by(Profession.position, Profession.label)
        ).all())

    def get(self, profession_id: int) -> Profession:
        profession = self._db.get(Profession, profession_id)
        if profession is None:
            raise NotFound("Profession not found")
        return profession

    def create(self, data: dict) -> Profession:
        if self._db.scalar(select(Profession).where(Profession.key == data["key"])):
            raise Conflict("A profession with this key already exists")
        profession = Profession(**data)
        self._db.add(profession)
        self._db.commit()
        self._db.refresh(profession)
        return profession

    def update(self, profession_id: int, data: dict) -> Profession:
        profession = self.get(profession_id)
        if "key" in data and data["key"] != profession.key:
            if self._db.scalar(select(Profession).where(Profession.key == data["key"])):
                raise Conflict("A profession with this key already exists")
        for name, value in data.items():
            setattr(profession, name, value)
        self._db.commit()
        self._db.refresh(profession)
        return profession

    # ── the owner-defined form ──────────────────────────────────────────────
    def list_fields(self, profession_id: int) -> list[ProfessionField]:
        self.get(profession_id)
        return list(self._db.scalars(
            select(ProfessionField)
            .where(ProfessionField.profession_id == profession_id)
            .order_by(ProfessionField.position, ProfessionField.label)
        ).all())

    def get_field(self, profession_id: int, field_id: int) -> ProfessionField:
        field = self._db.get(ProfessionField, field_id)
        # The profession_id check matters: without it,
        # /professions/1/fields/99 would happily edit profession 2's field.
        if field is None or field.profession_id != profession_id:
            raise NotFound("Field not found")
        return field

    def create_field(self, profession_id: int, data: dict) -> ProfessionField:
        self.get(profession_id)
        self._check_options(data["type"], data.get("options"))

        clash = self._db.scalar(select(ProfessionField).where(
            ProfessionField.profession_id == profession_id,
            ProfessionField.key == data["key"],
        ))
        if clash is not None:
            raise Conflict("This profession already has a field with that key")

        field = ProfessionField(profession_id=profession_id, **data)
        self._db.add(field)
        self._db.commit()
        self._db.refresh(field)
        return field

    def update_field(self, profession_id: int, field_id: int, data: dict) -> ProfessionField:
        field = self.get_field(profession_id, field_id)
        # `key` is deliberately absent from the update schema: it is what every
        # stored answer is filed under, so renaming it would orphan the data.
        if "options" in data:
            self._check_options(field.type, data["options"])
        for name, value in data.items():
            setattr(field, name, value)
        self._db.commit()
        self._db.refresh(field)
        return field

    def delete_field(self, profession_id: int, field_id: int) -> None:
        field = self.get_field(profession_id, field_id)
        self._db.delete(field)
        self._db.commit()
        # Answers already stored under this key stay in professionals.custom
        # but become unreachable: the public serialiser is driven by field
        # definitions, so a value with no definition can never be rendered.
        # Deleting the definition is therefore enough to un-publish it.

    def _check_options(self, field_type: FieldType, options: list[str] | None) -> None:
        if field_type not in CHOICE_TYPES:
            return
        if not options:
            raise Invalid("A choice field needs at least one option")
        if len(set(options)) != len(options):
            raise Invalid("Options must be unique")
