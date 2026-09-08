"""What a professional says they do, chosen from a list their trade decides.

The column was free text: whatever a caller typed, one entry per line. Two
agents could describe the same service in two different sentences and neither
could be matched against the other, which makes the one question a buyer
actually has — does this person do the thing I need — unanswerable by reading
two profiles side by side.

So the trade owns the vocabulary and the limit, and these say the trade is
enforced rather than merely suggested.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.domain.errors import Invalid
from app.domain.specializations import clean
from app.models import Profession, Professional, Stage


LAWYER = ["Golden Visa", "Real Estate Law", "Property Transactions", "Contract Law"]


@pytest.fixture
def trade(db, professions):
    """The lawyer trade, with a vocabulary and a limit of three."""
    row = db.get(Profession, professions["lawyer"])
    row.specializations = LAWYER
    row.max_specializations = 3
    db.commit()
    return row


def _pro(db, professions, **kw):
    base = dict(
        business_name="Papadopoulos & Partners", contact_name="Kostas Papadopoulos",
        slug="kostas-papadopoulos", city="Heraklion", region="Crete",
        profession_id=professions["lawyer"],
        published=True, stage=Stage.signed,
        published_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


# ── the rule on its own ──────────────────────────────────────────────────────
def test_the_list_comes_back_in_the_trades_order():
    """Two professionals of one trade list their services in one sequence, so
    a buyer comparing two profiles is reading two of the same thing."""
    assert clean(["Contract Law", "Golden Visa"], LAWYER, 3) == [
        "Golden Visa", "Contract Law",
    ]


def test_a_service_the_trade_does_not_offer_is_refused():
    with pytest.raises(Invalid):
        clean(["Structural Engineering"], LAWYER, 3)


def test_more_than_the_trade_allows_is_refused():
    """Refused rather than trimmed: dropping the fourth tick silently would
    tell a caller they had saved something they had not."""
    with pytest.raises(Invalid):
        clean(LAWYER, LAWYER, 3)


def test_blanks_and_repeats_do_not_count_towards_the_limit():
    assert clean(["Golden Visa", "Golden Visa", "  "], LAWYER, 1) == ["Golden Visa"]


def test_a_trade_with_no_list_takes_what_it_is_given():
    """Not a transitional state. It is what lets the owner add a trade without
    inventing its vocabulary in the same breath."""
    assert clean(["anything at all"], None, None) == ["anything at all"]


def test_a_trade_with_no_list_still_honours_a_limit():
    with pytest.raises(Invalid):
        clean(["one", "two"], None, 1)


# ── through the API ──────────────────────────────────────────────────────────
def test_a_caller_ticks_from_the_trades_list(as_caller, db, professions, trade):
    p = _pro(db, professions)
    r = as_caller.patch(f"/api/professionals/{p.id}",
                        json={"specialties": ["Contract Law", "Golden Visa"]})
    assert r.status_code == 200, r.text
    db.refresh(p)
    assert p.specialties == ["Golden Visa", "Contract Law"]


def test_the_api_refuses_a_service_from_another_trade(as_caller, db, professions, trade):
    p = _pro(db, professions)
    r = as_caller.patch(f"/api/professionals/{p.id}",
                        json={"specialties": ["Structural Engineering"]})
    assert r.status_code == 422


def test_the_api_refuses_more_than_the_trade_allows(as_caller, db, professions, trade):
    p = _pro(db, professions)
    r = as_caller.patch(f"/api/professionals/{p.id}", json={"specialties": LAWYER})
    assert r.status_code == 422


def test_moving_trade_drops_the_services(as_caller, db, professions, trade):
    """"Golden Visa" is a lawyer's. An architect who inherited it would be
    publishing a claim nobody made about them."""
    p = _pro(db, professions, specialties=["Golden Visa"])
    r = as_caller.patch(f"/api/professionals/{p.id}",
                        json={"profession_id": professions["architect"]})
    assert r.status_code == 200, r.text
    db.refresh(p)
    assert not p.specialties


def test_clearing_the_services_is_allowed(as_caller, db, professions, trade):
    p = _pro(db, professions, specialties=["Golden Visa"])
    as_caller.patch(f"/api/professionals/{p.id}", json={"specialties": None})
    db.refresh(p)
    assert p.specialties is None


# ── what the buyer gets ──────────────────────────────────────────────────────
def test_the_profile_publishes_them_in_the_trades_order(
    client, as_caller, db, professions, trade,
):
    p = _pro(db, professions)
    as_caller.patch(f"/api/professionals/{p.id}",
                    json={"specialties": ["Contract Law", "Golden Visa"]})
    body = client.get("/api/public/professionals/kostas-papadopoulos").json()
    assert body["specialties"] == ["Golden Visa", "Contract Law"]


# ── the trade's own list is the owner's to edit ──────────────────────────────
def test_the_owner_can_rewrite_the_trades_list(as_owner, db, professions, trade):
    r = as_owner.patch(f"/api/professions/{trade.id}", json={
        "specializations": ["Tax Law", "Corporate Law"], "max_specializations": 2,
    })
    assert r.status_code == 200, r.text
    db.refresh(trade)
    assert trade.specializations == ["Tax Law", "Corporate Law"]
    assert trade.max_specializations == 2


def test_a_trade_seeded_with_no_list_reads_back_as_none(as_owner, db, professions):
    r = as_owner.get("/api/professions")
    rows = r.json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    architect = next(row for row in rows if row["key"] == "architect")
    assert architect["specializations"] is None
    assert architect["max_specializations"] is None
