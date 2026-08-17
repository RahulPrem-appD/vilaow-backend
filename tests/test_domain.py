"""The business rules, tested without a database, a request or a mail server.

This file is the point of the domain layer. Every assertion below used to
require standing up Postgres, creating a professional, issuing an agreement
over HTTP and reading a status code back — which made the rules slow to test,
awkward to test exhaustively, and impossible to reuse outside the web app.

Nothing here imports FastAPI or SQLAlchemy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from app.domain.fields import coerce, is_blank, missing_required, public_values, validate_custom
from app.domain.publishing import PublishFacts, evaluate, needs_attention
from app.ports.clock import FrozenClock


@dataclass
class F:
    """A field definition, without the ORM."""
    key: str
    label: str = "A field"
    type: object = None
    options: list | None = None
    required: bool = False
    public: bool = False
    position: int = 0
    active: bool = True


def field(key, type_, **kw):
    from app.models import FieldType
    return F(key=key, type=getattr(FieldType, type_), **kw)


# ── the publish gate ────────────────────────────────────────────────────────
def _facts(**kw) -> PublishFacts:
    base = dict(has_profession=True, agreement_signed=True, email_confirmed=True,
                has_photo=True, required_fields=(), answers={})
    base.update(kw)
    return PublishFacts(**base)


def test_all_four_gates_satisfied_is_ready():
    assert evaluate(_facts()).ready is True


def test_an_unsigned_agreement_blocks_publication():
    """Clause 5 is the permission to display a name and photo at all."""
    state = evaluate(_facts(agreement_signed=False, email_confirmed=False))
    assert state.ready is False
    assert any("has not been signed" in b for b in state.blockers)


def test_signed_but_unconfirmed_is_a_different_sentence():
    """Different jobs for a caller: one is a phone call, the other is asking
    someone to check their inbox. Collapsing them would hide which."""
    state = evaluate(_facts(email_confirmed=False))
    assert any("not been confirmed" in b for b in state.blockers)
    assert not any("has not been signed" in b for b in state.blockers)


def test_a_missing_photo_blocks_publication():
    assert any("No photo" in b for b in evaluate(_facts(has_photo=False)).blockers)


def test_a_missing_required_answer_blocks_and_names_the_key():
    bar = field("bar_number", "short_text", label="Bar number", required=True)
    state = evaluate(_facts(required_fields=(bar,), answers={}))
    assert state.ready is False
    assert "bar_number" in state.missing_field_keys
    assert any("Bar number is required" in b for b in state.blockers)


def test_an_answered_required_field_does_not_block():
    bar = field("bar_number", "short_text", required=True)
    assert evaluate(_facts(required_fields=(bar,), answers={"bar_number": "BAR-1"})).ready


def test_adding_a_requirement_produces_a_backlog_not_an_unpublish():
    """The rule that keeps buyers off dead links. `needs_attention` reports the
    gap; nothing in the domain takes a profile down."""
    bar = field("bar_number", "short_text", required=True)
    assert needs_attention(True, [bar], {}) == ["bar_number"]
    # Unpublished records are not a backlog — they are not on the site.
    assert needs_attention(False, [bar], {}) == []


# ── what a stranger may see ─────────────────────────────────────────────────
def test_an_internal_field_is_never_projected():
    internal = field("best_time", "short_text", public=False)
    assert public_values([internal], {"best_time": "Tuesdays"}) == []


def test_a_public_field_is_projected_with_its_label():
    bar = field("bar_number", "short_text", label="Bar number", public=True)
    assert public_values([bar], {"bar_number": "BAR-1"}) == [
        {"key": "bar_number", "label": "Bar number", "type": "short_text", "value": "BAR-1"}
    ]


def test_a_value_with_no_definition_behind_it_cannot_be_projected():
    """The direction that matters: projection reads the definitions and looks
    values up, never the reverse. A key left behind by a deleted field — or by
    one later switched to internal — has no way to reach a page."""
    assert public_values([], {"orphaned": "still in the json"}) == []


def test_documents_are_never_projected_even_when_marked_public():
    doc = field("insurance", "file", public=True)
    assert public_values([doc], {"insurance": 7}) == []


# ── answer validation ───────────────────────────────────────────────────────
def test_a_choice_outside_the_allowed_options_is_refused():
    spec = field("specialism", "select", options=["Conveyancing", "Litigation"])
    with pytest.raises(Exception) as caught:
        coerce(spec, "Astrology")
    assert "allowed options" in str(caught.value)


def test_multi_select_deduplicates_and_keeps_order():
    langs = field("langs", "multi_select", options=["English", "Greek", "German"])
    assert coerce(langs, ["Greek", "English", "Greek"]) == ["Greek", "English"]


def test_a_date_is_normalised_and_a_non_date_refused():
    when = field("expiry", "date")
    assert coerce(when, "2026-03-01") == "2026-03-01"
    with pytest.raises(Exception):
        coerce(when, "next Tuesday")


def test_a_boolean_is_not_a_number():
    """bool is an int in Python; accepting True as a number would silently
    store a nonsense answer."""
    years = field("years", "number")
    with pytest.raises(Exception):
        coerce(years, True)


def test_blank_shapes_all_mean_not_answered():
    assert is_blank(None) and is_blank("") and is_blank("   ") and is_blank([])
    assert not is_blank(0) and not is_blank("x")


def test_unknown_keys_are_dropped_rather_than_stored():
    bar = field("bar_number", "short_text")
    merged, errors = validate_custom([bar], {"bar_number": "B1", "junk": "x"})
    assert merged == {"bar_number": "B1"}
    assert errors == []


def test_a_partial_submission_merges_over_what_is_stored():
    a = field("a", "short_text")
    b = field("b", "short_text")
    merged, _ = validate_custom([a, b], {"b": "new"}, partial=True,
                                existing={"a": "kept", "b": "old"})
    assert merged == {"a": "kept", "b": "new"}


def test_a_required_field_left_blank_is_reported_by_key():
    bar = field("bar_number", "short_text", label="Bar number", required=True)
    assert [f.key for f in missing_required([bar], {"bar_number": "  "})] == ["bar_number"]


# ── the clock port ──────────────────────────────────────────────────────────
def test_a_frozen_clock_makes_time_rules_testable_without_back_dating_rows():
    start = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    clock = FrozenClock(start)
    assert clock.now() == start
    clock.advance(timedelta(days=4))
    assert clock.now() - start == timedelta(days=4)


def test_a_frozen_clock_refuses_a_naive_datetime():
    """A naive timestamp on a signed agreement is a legal record with an
    ambiguous time zone."""
    with pytest.raises(ValueError):
        FrozenClock(datetime(2026, 8, 13, 12, 0))
