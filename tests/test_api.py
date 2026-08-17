"""The API's promises, exercised over HTTP.

Weighted towards the things that would actually hurt: an admin anyone can read,
a caller who can publish, a buyer callback that loses its 24-hour deadline, a
signing link that works twice.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from app.models import Professional, Stage

ADMIN_GETS = [
    "/api/professionals",
    "/api/imports",
    "/api/leads",
    "/api/professions",
    "/api/staff",
]


# ── nobody gets in without signing in ───────────────────────────────────────
@pytest.mark.parametrize("path", ADMIN_GETS)
def test_admin_endpoints_refuse_anonymous_callers(client, path):
    """Two earlier versions of this admin were reachable by anyone with the
    URL. Every admin route is checked, not a sample."""
    assert client.get(path).status_code == 401


def test_health_is_public(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_rejects_a_wrong_password(client, owner):
    r = client.post("/api/auth/login", json={"email": owner.email, "password": "wrong"})
    assert r.status_code == 401


def test_login_rejects_a_deactivated_account(client, db, caller):
    caller.active = False
    db.commit()
    r = client.post("/api/auth/login", json={"email": caller.email, "password": "caller-pw"})
    assert r.status_code == 401


def test_login_then_me(as_owner, owner):
    r = as_owner.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["email"] == owner.email


def test_a_password_hash_is_never_returned(as_owner):
    assert "password" not in as_owner.get("/api/auth/me").text.lower()
    assert "password_hash" not in as_owner.get("/api/staff").text


def test_logout_ends_the_session(as_owner):
    as_owner.post("/api/auth/logout")
    assert as_owner.get("/api/auth/me").status_code == 401


def test_a_forged_cookie_is_rejected(client):
    """The cookie is signed; editing it must not grant access."""
    client.cookies.set("vilaow_session", "eyJzaWQiOjF9.forged.signature")
    assert client.get("/api/auth/me").status_code == 401


# ── a caller is not an owner ────────────────────────────────────────────────
def test_a_caller_cannot_list_staff(as_caller):
    assert as_caller.get("/api/staff").status_code == 403


def test_a_caller_cannot_create_a_profession(as_caller):
    r = as_caller.post("/api/professions", json={
        "key": "surveyor", "label": "Surveyor", "plural": "Surveyors"})
    assert r.status_code == 403


def test_an_owner_can_create_a_profession(as_owner):
    r = as_owner.post("/api/professions", json={
        "key": "surveyor", "label": "Surveyor", "plural": "Surveyors"})
    assert r.status_code in (200, 201)


# ── publishing ──────────────────────────────────────────────────────────────
def _a_professional(db, professions, **kw) -> Professional:
    p = Professional(business_name="Test Firm", phone="+30 210 000 0000",
                     city="Athens", region="Athens",
                     profession_id=professions["lawyer"], **kw)
    db.add(p)
    db.commit()
    return p


def test_a_caller_cannot_publish(as_caller, db, professions):
    p = _a_professional(db, professions, stage=Stage.signed)
    assert as_caller.post(f"/api/professionals/{p.id}/publish").status_code == 403


def test_publishing_is_refused_without_a_signed_agreement(as_owner, db, professions):
    """His listing agreement is what permits us to show someone's details.
    Publishing before it is signed would put a real person on a public site
    without their consent."""
    p = _a_professional(db, professions, stage=Stage.contacted)
    r = as_owner.post(f"/api/professionals/{p.id}/publish")
    assert r.status_code == 409


# ── the 24-hour callback ────────────────────────────────────────────────────
def test_a_buyer_can_ask_for_a_callback_without_signing_in(client):
    r = client.post("/api/leads", json={
        "buyer_name": "A Buyer", "buyer_phone": "+44 7700 900000",
        "message": "Please call me"})
    assert r.status_code in (200, 201)


def test_a_callback_gets_a_deadline_24_hours_out(client, db):
    """The site promises 24 hours. If the deadline is not stamped on insert
    there is nothing to be late against."""
    from app.models import Lead
    client.post("/api/leads", json={"buyer_name": "B", "buyer_phone": "+44 7700 900001"})
    lead = db.query(Lead).one()
    assert lead.callback_due is not None
    gap = lead.callback_due - lead.created_at
    assert timedelta(hours=23, minutes=59) <= gap <= timedelta(hours=24, minutes=1)


def test_leads_are_not_readable_by_the_public(client):
    client.post("/api/leads", json={"buyer_name": "C", "buyer_phone": "+44 7700 900002"})
    assert client.get("/api/leads").status_code == 401


def test_the_overdue_filter_finds_a_late_callback(as_caller, db):
    from app.models import Lead, LeadStatus
    late = Lead(buyer_name="Late", buyer_phone="+44 7700 900003",
                status=LeadStatus.new,
                callback_due=datetime.now(timezone.utc) - timedelta(hours=2))
    db.add(late)
    db.commit()
    r = as_caller.get("/api/leads", params={"overdue": True})
    assert r.status_code == 200
    assert any(x["buyer_name"] == "Late" for x in _items(r.json()))


def _items(payload):
    return payload["items"] if isinstance(payload, dict) and "items" in payload else payload


# ── the signing link ────────────────────────────────────────────────────────
def test_an_unknown_signing_token_is_not_found(client):
    assert client.get("/api/agreements/nonexistent-token").status_code == 404


SIGN_BODY = {
    "signed_name": "Kostas P",
    "signature_image": "data:image/png;base64,iVBORw0KGgo=",
    "licence": "ABC123",
    "vat_number": "EL123456789",
    "email": "k@example.com",
    "phone": "+30 210 111 1111",
    "agreed": True,
}




def test_a_signing_link_cannot_be_used_twice(as_owner, client, db, professions, codes):
    """It is a one-time link sent to one person. Replaying it would let a
    second signature overwrite the first."""
    p = _a_professional(db, professions, stage=Stage.details_collected)
    issued = as_owner.post(f"/api/agreements/issue/{p.id}")
    assert issued.status_code in (200, 201), issued.text
    token = issued.json()["token"]

    assert client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY).status_code in (200, 201)
    assert client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY).status_code == 410


def test_the_drawn_signature_is_actually_stored(as_owner, client, db, professions, codes):
    """Regression: renaming this column left the router assigning to an
    attribute the model no longer had. SQLAlchemy accepts that silently, so
    every signature was being discarded while the endpoint returned 200.
    Asserting on the response would not have caught it — only the row does.
    """
    from app.models import Agreement

    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    assert client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY).status_code in (200, 201)

    stored = db.query(Agreement).filter(Agreement.token == token).one()
    db.refresh(stored)
    assert stored.signature_image == SIGN_BODY["signature_image"]
    assert stored.signed_ip is not None
    assert stored.signed_email == "k@example.com"


def test_the_terms_are_frozen_onto_the_agreement(as_owner, client, db, professions):
    """The PDF is rendered on demand, so the words have to live on the row.
    A version string alone cannot reproduce what someone agreed to once the
    published wording has moved on."""
    from app.models import Agreement

    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]

    stored = db.query(Agreement).filter(Agreement.token == token).one()
    assert stored.terms_text
    assert "Greek law applies." in stored.terms_text
    # And the page is served the same words it will be held to.
    served = client.get(f"/api/agreements/{token}").json()
    assert served["clauses"][-1] == "Greek law applies."


def test_signing_without_ticking_the_box_is_refused(as_owner, client, db, professions):
    """The tick is the agreement. The page enforces it, but the page is not
    the thing we can trust."""
    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    r = client.post(f"/api/agreements/{token}/sign", json={**SIGN_BODY, "agreed": False})
    assert r.status_code == 422


def test_signing_alone_does_not_reach_signed(as_owner, client, db, professions, codes):
    """A signature with an unconfirmed address is evidence of something, but
    not of who. The publish gate must stay shut until the code is entered."""
    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY)

    db.refresh(p)
    assert p.stage == Stage.signature_sent


def test_the_emailed_code_completes_the_signing(as_owner, client, db, professions, codes):
    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY)

    assert codes, "signing should have sent a confirmation code"
    r = client.post(f"/api/agreements/{token}/verify", json={"code": codes[-1]})
    assert r.status_code == 200, r.text

    db.refresh(p)
    assert p.stage == Stage.signed


def test_a_wrong_code_is_refused_and_runs_out_of_attempts(as_owner, client, db, professions, codes):
    """Five tries against six digits, then it stops. Without counting the
    failures the limit would be decorative."""
    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY)

    wrong = "000000" if codes[-1] != "000000" else "111111"
    for _ in range(5):
        assert client.post(f"/api/agreements/{token}/verify", json={"code": wrong}).status_code == 400

    # Even the right code is refused once the attempts are spent.
    assert client.post(f"/api/agreements/{token}/verify", json={"code": codes[-1]}).status_code == 429
    db.refresh(p)
    assert p.stage == Stage.signature_sent


def test_a_used_code_cannot_be_replayed(as_owner, client, db, professions, codes):
    from app.models import Agreement

    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY)
    client.post(f"/api/agreements/{token}/verify", json={"code": codes[-1]})

    stored = db.query(Agreement).filter(Agreement.token == token).one()
    db.refresh(stored)
    assert stored.otp_hash is None
    assert stored.email_verified_at is not None


def test_issuing_an_agreement_requires_signing_in(client, db, professions):
    p = _a_professional(db, professions)
    assert client.post(f"/api/agreements/issue/{p.id}").status_code == 401


# ── the audit trail ─────────────────────────────────────────────────────────
def test_a_stage_change_is_recorded_against_the_person_who_made_it(as_caller, db, professions, caller):
    from app.models import Event
    p = _a_professional(db, professions)
    r = as_caller.post(f"/api/professionals/{p.id}/stage", json={"stage": "contacted"})
    assert r.status_code in (200, 201), r.text
    ev = db.query(Event).filter(Event.professional_id == p.id).all()
    assert ev, "a stage change wrote no event"
    assert any(e.actor_label for e in ev), "an event with no actor is not an audit trail"


def test_the_signed_agreement_can_be_produced_as_a_pdf(as_owner, client, db, professions, codes):
    """The mock's "download signed copy" was window.print(), so Vilaow held
    nothing. This is the copy Vilaow keeps, rendered from the stored row."""
    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY)
    client.post(f"/api/agreements/{token}/verify", json={"code": codes[-1]})

    by_token = client.get(f"/api/agreements/{token}/pdf")
    assert by_token.status_code == 200, by_token.text
    assert by_token.headers["content-type"] == "application/pdf"
    assert by_token.content.startswith(b"%PDF")
    assert "no-store" in by_token.headers["cache-control"]

    staff_copy = as_owner.get(f"/api/agreements/professional/{p.id}/pdf")
    assert staff_copy.status_code == 200
    assert staff_copy.content.startswith(b"%PDF")


def test_an_unsigned_agreement_has_no_pdf(as_owner, client, db, professions):
    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    assert client.get(f"/api/agreements/{token}/pdf").status_code == 404


def test_a_broken_signature_image_still_produces_a_document(as_owner, client, db, professions, codes):
    """A PDF without the ink is far more useful than a 500 when somebody needs
    the agreement."""
    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    client.post(f"/api/agreements/{token}/sign",
                json={**SIGN_BODY, "signature_image": "data:image/png;base64,!!not-base64!!"})

    r = client.get(f"/api/agreements/{token}/pdf")
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_a_signed_agreement_stays_readable_so_the_code_can_be_entered(
    as_owner, client, db, professions, codes
):
    """Regression: GET refused a signed agreement with 410, which meant anyone
    who refreshed the tab after signing could never enter their confirmation
    code. The agreement then sat at signature_sent forever and the profile
    could never be published.

    Signing is two steps; the page has to survive between them.
    """
    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]
    client.post(f"/api/agreements/{token}/sign", json=SIGN_BODY)

    again = client.get(f"/api/agreements/{token}")
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["signed_at"] is not None
    assert body["email_verified_at"] is None      # the outstanding step
    assert body["signed_email"] == "k@example.com"

    # And the code still works after that reload.
    assert client.post(f"/api/agreements/{token}/verify",
                       json={"code": codes[-1]}).status_code == 200


def test_an_unsigned_expired_link_is_still_gone(as_owner, client, db, professions):
    """Relaxing the rule above must not make dead links work."""
    from datetime import datetime, timedelta, timezone
    from app.models import Agreement

    p = _a_professional(db, professions, stage=Stage.details_collected)
    token = as_owner.post(f"/api/agreements/issue/{p.id}").json()["token"]

    stored = db.query(Agreement).filter(Agreement.token == token).one()
    stored.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    db.commit()

    assert client.get(f"/api/agreements/{token}").status_code == 410
