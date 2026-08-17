"""The owner-defined form: validating answers, and deciding what is public.

This module is the single place that knows how to read a `ProfessionField`
definition. Two jobs, and the second one is the important one:

  1. Coerce and validate a caller's answer against the field that defines it.
  2. Decide which answers a stranger is allowed to see.

Job 2 exists because the public API is a deliberately separate layer with its
own schemas, so that internal columns cannot leak. Owner-defined fields are the
one thing that could punch a hole in that guarantee — a field invented after the
code was written, rendered by a template that trusts whatever it is handed.

So the rule is inverted here: `public_values()` starts from the *field
definitions*, not from the stored data, and emits a value only when its field
says `public`. A key sitting in `professionals.custom` with no matching public
field definition is unreachable from the outside no matter how it got there —
including keys left behind by a field that was later switched to internal.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Iterable

from app.models import FieldType, ProfessionField

# Generous, but bounded: a public profile should not be able to carry an essay,
# and an unbounded text column is a denial-of-service waiting to happen.
MAX_SHORT_TEXT = 240
MAX_LONG_TEXT = 4000


class FieldError(ValueError):
    """A value that does not satisfy its field definition."""

    def __init__(self, key: str, message: str) -> None:
        self.key = key
        self.message = message
        super().__init__(f"{key}: {message}")


def is_blank(value: Any) -> bool:
    """Whether an answer counts as "not filled in".

    Empty string, empty list and None all mean the same thing to a caller
    looking at a form, so they have to mean the same thing to the publish gate.
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    if isinstance(value, (list, tuple, dict)) and len(value) == 0:
        return True
    return False


def coerce(field: ProfessionField, value: Any) -> Any:
    """Validate one answer and return it in the shape it should be stored.

    Raises FieldError. Blank values come back as None so that "" and [] never
    linger in the JSON as if they were answers.
    """
    if is_blank(value):
        return None

    match field.type:
        case FieldType.short_text | FieldType.long_text:
            if not isinstance(value, str):
                raise FieldError(field.key, "expected text")
            text = value.strip()
            limit = MAX_SHORT_TEXT if field.type == FieldType.short_text else MAX_LONG_TEXT
            if len(text) > limit:
                raise FieldError(field.key, f"longer than {limit} characters")
            return text

        case FieldType.select:
            if not isinstance(value, str):
                raise FieldError(field.key, "expected one option")
            if value not in (field.options or []):
                raise FieldError(field.key, "not one of the allowed options")
            return value

        case FieldType.multi_select:
            if not isinstance(value, (list, tuple)):
                raise FieldError(field.key, "expected a list of options")
            allowed = set(field.options or [])
            chosen = list(dict.fromkeys(value))  # de-duplicate, keep order
            for item in chosen:
                if item not in allowed:
                    raise FieldError(field.key, f"'{item}' is not one of the allowed options")
            return chosen

        case FieldType.number:
            if isinstance(value, bool):  # bool is an int in Python; not a number here
                raise FieldError(field.key, "expected a number")
            if isinstance(value, (int, float)):
                return _finite(field, value)
            try:
                text = str(value).strip()
                parsed = int(text) if text.lstrip("-").isdigit() else float(text)
            except (TypeError, ValueError):
                raise FieldError(field.key, "expected a number") from None
            return _finite(field, parsed)

        case FieldType.date:
            if isinstance(value, date):
                return value.isoformat()
            try:
                return date.fromisoformat(str(value).strip()).isoformat()
            except (TypeError, ValueError):
                raise FieldError(field.key, "expected a date as YYYY-MM-DD") from None

        case FieldType.file:
            # Stored as the id of an Asset row. The upload endpoint creates the
            # asset; this only records which one answers this field.
            try:
                return int(value)
            except (TypeError, ValueError):
                raise FieldError(field.key, "expected an uploaded file reference") from None

    raise FieldError(field.key, f"unsupported field type {field.type}")


def validate_custom(
    fields: Iterable[ProfessionField],
    submitted: dict[str, Any],
    *,
    partial: bool = True,
    existing: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[FieldError]]:
    """Validate a set of answers against a profession's fields.

    Unknown keys are dropped rather than stored: a value with no field to
    describe it can never be rendered, validated or made public, so keeping it
    would only be a place for junk to accumulate.

    `partial=True` merges over `existing`, so a form that submits one field does
    not wipe the rest.
    """
    by_key = {f.key: f for f in fields if f.active}
    result: dict[str, Any] = dict(existing or {}) if partial else {}
    errors: list[FieldError] = []

    for key, raw in (submitted or {}).items():
        field = by_key.get(key)
        if field is None:
            continue
        try:
            coerced = coerce(field, raw)
        except FieldError as exc:
            errors.append(exc)
            continue
        if coerced is None:
            result.pop(key, None)
        else:
            result[key] = coerced

    return result, errors


def missing_required(
    fields: Iterable[ProfessionField],
    custom: dict[str, Any] | None,
) -> list[ProfessionField]:
    """Required fields with no answer.

    Used by the publish gate, and by the "needs attention" view — which exists
    because adding a required field to a live profession must never unpublish
    anyone. It raises a backlog instead.
    """
    values = custom or {}
    return [
        f for f in fields
        if f.active and f.required and is_blank(values.get(f.key))
    ]


def public_values(
    fields: Iterable[ProfessionField],
    custom: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """The answers a stranger may see, in the owner's display order.

    Driven by the field definitions rather than by the stored keys — see the
    module docstring. This is the function that must stay honest; everything
    else here is convenience.
    """
    values = custom or {}
    out: list[dict[str, Any]] = []

    for field in sorted(
        (f for f in fields if f.active and f.public),
        key=lambda f: (f.position, f.label),
    ):
        # A file field is a stored asset id. Publishing that id would be
        # meaningless to a reader and an invitation to probe the endpoint, so
        # documents simply do not appear publicly.
        if field.type == FieldType.file:
            continue

        value = values.get(field.key)
        if is_blank(value):
            continue

        out.append({
            "key": field.key,
            "label": field.label,
            "type": field.type.value,
            "value": value,
        })

    return out
