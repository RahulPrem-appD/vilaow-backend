"""Number fields — the type nothing else in the suite ever constructed.

That gap is the whole reason this file exists. `coerce()` called a `_finite`
helper that was never written, so every answer to a number field raised
NameError, and 278 tests stayed green because not one of them built a
`FieldType.number`. A caller typing a year into an owner-defined field got a
500; so did a professional signing a form that had one.

These tests are checked against the broken version first: with `_finite`
removed they fail with NameError, which is the only way to know a guard would
have caught the thing it claims to catch.
"""
import math

import pytest

from app.domain.fields import FieldError, coerce
from app.models import FieldType, ProfessionField


def number_field(key: str = "years") -> ProfessionField:
    return ProfessionField(key=key, label="Years", type=FieldType.number,
                           active=True, public=True, required=False)


@pytest.mark.parametrize("given, expected", [
    (12, 12), (0, 0), (-4, -4), (3.5, 3.5),
    ("12", 12), (" 12 ", 12), ("-4", -4), ("3.5", 3.5), ("1e3", 1000.0),
])
def test_a_real_number_survives(given, expected):
    assert coerce(number_field(), given) == expected


@pytest.mark.parametrize("given", [
    float("inf"), float("-inf"), float("nan"),
    # The string forms matter as much as the float ones: this is what arrives
    # over JSON, and float() accepts every one of them.
    "Infinity", "-Infinity", "NaN", "inf", "nan",
    # Overflows to inf rather than raising, so it reaches the same column.
    "1e400",
])
def test_a_number_postgres_cannot_store_is_refused(given):
    """JSONB has no Infinity and no NaN. Refuse at the boundary, not on insert."""
    with pytest.raises(FieldError):
        coerce(number_field(), given)


@pytest.mark.parametrize("given", ["twelve", "12abc", "1,2", "0x10"])
def test_something_that_is_not_a_number_is_refused(given):
    with pytest.raises(FieldError):
        coerce(number_field(), given)


@pytest.mark.parametrize("given", ["", "   ", None, [], {}])
def test_a_blank_answer_is_none_and_not_an_error(given):
    """Blank is "not filled in", not "wrong" — see `is_blank`. A number field
    that raised on an empty box would make every optional field required."""
    assert coerce(number_field(), given) is None


def test_a_bool_is_not_a_number():
    """`True` is an `int` in Python. It is not an answer to "how many years"."""
    for value in (True, False):
        with pytest.raises(FieldError):
            coerce(number_field(), value)


def test_what_comes_back_is_json_serialisable():
    """The point of the guard: whatever survives must reach JSONB intact."""
    import json
    for given in (12, "12", 3.5, "-0.5"):
        value = coerce(number_field(), given)
        assert math.isfinite(value)
        json.loads(json.dumps(value))


def test_a_multi_select_with_an_unhashable_item_is_422_not_500():
    """`dict.fromkeys` hashes every item, so a nested list raised TypeError.

    `validate_custom` catches only FieldError, and the app installs a handler
    only for DomainError, so a malformed answer came back as a 500 while every
    sibling branch of `coerce` returns 422.
    """
    from app.models import FieldType, ProfessionField

    field = ProfessionField(key="langs", label="Languages",
                            type=FieldType.multi_select, active=True, public=True,
                            required=False, options=["Greek", "English"])
    for given in ([["el"]], [{"a": 1}], [["a"], "Greek"]):
        with pytest.raises(FieldError):
            coerce(field, given)
    assert coerce(field, ["Greek", "Greek"]) == ["Greek"]


def test_the_json_columns_refuse_what_postgres_cannot_store():
    """`costs`, `faq` and `custom` are `list[Any]`/`dict[str, Any]` — no type
    stops NaN. FastAPI parses with `json.loads`, which accepts the literals, so
    they reached JSONB and failed on insert as a 500. Same class as `_finite`,
    two columns over.
    """
    import json

    from pydantic import ValidationError

    from app.schemas.professionals import ProfessionalUpdate

    ProfessionalUpdate(**json.loads('{"costs": [1, 2.5], "faq": [{"q": "a"}]}'))
    for body in ('{"costs": [Infinity]}', '{"faq": [{"a": NaN}]}',
                 '{"custom": {"x": [-Infinity]}}'):
        with pytest.raises(ValidationError):
            ProfessionalUpdate(**json.loads(body))
