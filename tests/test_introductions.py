"""Introductions — the one place a stranger can cause the system to act.

Everything else public is a read. This endpoint takes untrusted input and makes
the platform email a real person's name, phone number and address to a third
party, so the tests below are mostly attempts to abuse it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.models import Introduction, Professional, Review, Stage


def _published(db, professions, **kw):
    base = dict(
        business_name="Papadopoulos & Partners",
        contact_name="Kostas Papadopoulos",
        email="kostas@example.com",
        city="Heraklion", region="Crete",
        profession_id=professions["lawyer"],
        slug="kostas", published=True, stage=Stage.signed,
        published_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    p = Professional(**base)
    db.add(p)
    db.commit()
    return p


def _body(**kw):
    base = {
        "slug": "kostas",
        "buyer_name": "Sarah Mitchell",
        "buyer_email": "sarah@example.com",
        "buyer_phone": "+44 7700 900123",
        "message": "Buying a house near Chania in the spring.",
        "consent": True,
    }
    base.update(kw)
    return base


# ── consent ─────────────────────────────────────────────────────────────────
def test_without_consent_nothing_is_sent_or_stored(client, db, professions, outbox):
    """The tick is the lawful basis for handing a stranger's details to a
    third party. The page enforces it; the page is not what we can trust."""
    _published(db, professions)
    r = client.post("/api/public/introductions", json=_body(consent=False))
    assert r.status_code == 422
    assert db.query(Introduction).count() == 0
    assert outbox.outbox == []


def test_consent_is_recorded_with_the_words_that_were_agreed(client, db, professions):
    _published(db, professions)
    client.post("/api/public/introductions", json=_body())

    intro = db.query(Introduction).one()
    assert intro.consent_at is not None
    assert "pass my name, email and phone" in intro.consent_text


# ── abuse ───────────────────────────────────────────────────────────────────
def test_the_honeypot_is_accepted_and_ignored(client, db, professions, outbox):
    """A bot that fills the hidden field gets a normal-looking success and no
    signal that it was caught — but nothing is stored and nobody is emailed."""
    _published(db, professions)
    r = client.post("/api/public/introductions", json=_body(website="http://spam.example"))
    assert r.status_code == 201
    assert db.query(Introduction).count() == 0
    assert outbox.outbox == []


def test_the_same_buyer_cannot_flood_the_directory(client, db, professions):
    """Enough for a household comparing three lawyers, and no more."""
    _published(db, professions)
    codes = [client.post("/api/public/introductions", json=_body()).status_code
             for _ in range(5)]
    assert codes.count(201) == 3
    assert codes[-1] == 429


def test_an_unpublished_professional_cannot_be_introduced(client, db, professions):
    """404 rather than 403, exactly as the read endpoints do it — otherwise
    this form enumerates who is in the pipeline."""
    _published(db, professions, published=False)
    assert client.post("/api/public/introductions", json=_body()).status_code == 404


# ── the two emails ──────────────────────────────────────────────────────────
def test_the_professional_gets_the_details_and_the_buyer_gets_the_promise(
    client, db, professions, outbox
):
    _published(db, professions)
    assert client.post("/api/public/introductions", json=_body()).status_code == 201

    to_professional = outbox.to("kostas@example.com")[0]
    assert "Sarah Mitchell" in to_professional.text
    assert "+44 7700 900123" in to_professional.text
    assert "sarah@example.com" in to_professional.text

    to_buyer = outbox.to("sarah@example.com")[0]
    assert "Kostas Papadopoulos" in to_buyer.text
    # The promise the caller queue exists to keep.
    assert "reach you shortly" in to_buyer.text


def test_a_failed_email_does_not_lose_the_introduction(client, db, professions):
    """A lost email is recoverable from the queue. A lost introduction is a
    buyer who was told someone would call and never heard from anyone.

    The failing transport is injected as a port implementation, the same way
    the real one is — no patching of anything the app owns.
    """
    from app.api.deps import get_email_sender
    from app.main import app
    from app.ports.email import SendResult

    class Broken:
        def send(self, message):
            return SendResult(False, "mail server down")

    app.dependency_overrides[get_email_sender] = Broken
    try:
        _published(db, professions)

        assert client.post("/api/public/introductions", json=_body()).status_code == 201
        assert db.query(Introduction).count() == 1
    finally:
        app.dependency_overrides.pop(get_email_sender, None)


# ── the audit trail ─────────────────────────────────────────────────────────
def _events(db, professional_id):
    from app.models import Event
    return {e.kind: e.detail for e in
            db.query(Event).filter_by(professional_id=professional_id).all()}


def test_both_introduction_emails_are_recorded_not_just_one(client, db, professions):
    """The professional's email was evented; the buyer's was not.

    That email is the whole promise — "Kostas will be in touch". When a buyer
    writes back saying nobody contacted them, the caller opening the record has
    to be able to see whether it was sent, and the absence of an event used to
    be indistinguishable from the absence of an email.
    """
    p = _published(db, professions)
    assert client.post("/api/public/introductions", json=_body()).status_code == 201

    events = _events(db, p.id)
    assert "introduction_emailed" in events
    assert "buyer_confirmation_sent" in events
    assert "sarah@example.com" in events["buyer_confirmation_sent"]


def test_a_failed_buyer_confirmation_is_recorded_as_a_failure(client, db, professions):
    """An event that says "sent" whether or not it was sent is worse than no
    event: it is a record that actively misleads whoever reads it."""
    from app.api.deps import get_email_sender
    from app.main import app
    from app.ports.email import SendResult

    class Broken:
        def send(self, message):
            return SendResult(False, "mail server down")

    app.dependency_overrides[get_email_sender] = Broken
    try:
        p = _published(db, professions)
        client.post("/api/public/introductions", json=_body())

        events = _events(db, p.id)
        assert "buyer_confirmation_failed" in events
        assert "buyer_confirmation_sent" not in events
        assert "mail server down" in events["buyer_confirmation_failed"]
    finally:
        app.dependency_overrides.pop(get_email_sender, None)


# ── the queue ───────────────────────────────────────────────────────────────
def _an_intro(client, db, professions, **kw):
    _published(db, professions)
    client.post("/api/public/introductions", json=_body(**kw))
    return db.query(Introduction).one()


def test_closing_without_saying_what_happened_is_refused(as_caller, client, db, professions):
    """The outcome is the only data here that says whether Vilaow works."""
    intro = _an_intro(client, db, professions)
    r = as_caller.patch(f"/api/introductions/{intro.id}", json={"status": "closed"})
    assert r.status_code == 422


def test_closing_with_an_outcome_stamps_who_and_when(as_caller, client, db, professions, caller):
    intro = _an_intro(client, db, professions)
    r = as_caller.patch(f"/api/introductions/{intro.id}",
                        json={"status": "closed", "outcome": "buyer_proceeded"})
    assert r.status_code == 200, r.text

    db.refresh(intro)
    assert intro.closed_at is not None
    assert intro.closed_by_id == caller.id


def test_overdue_means_still_open_not_merely_old(as_caller, client, db, professions):
    intro = _an_intro(client, db, professions)
    intro.due_at = datetime.now(timezone.utc) - timedelta(hours=2)
    db.commit()

    assert as_caller.get("/api/introductions?overdue=true").json()["total"] == 1

    as_caller.patch(f"/api/introductions/{intro.id}",
                    json={"status": "closed", "outcome": "no_response"})
    # Closed and long past due — but nobody is waiting on it any more.
    assert as_caller.get("/api/introductions?overdue=true").json()["total"] == 0


def test_the_queue_needs_a_login(client, db, professions):
    _an_intro(client, db, professions)
    assert client.get("/api/introductions").status_code == 401


# ── verified reviews ────────────────────────────────────────────────────────
def test_only_a_buyer_who_went_ahead_gets_a_review_token(as_caller, client, db, professions):
    intro = _an_intro(client, db, professions)
    as_caller.patch(f"/api/introductions/{intro.id}",
                    json={"status": "closed", "outcome": "buyer_went_elsewhere"})
    db.refresh(intro)
    assert intro.review_token is None

    as_caller.patch(f"/api/introductions/{intro.id}", json={"outcome": "buyer_proceeded"})
    db.refresh(intro)
    assert intro.review_token is not None


def test_a_review_token_for_someone_who_did_not_proceed_is_not_found(
    as_caller, client, db, professions
):
    intro = _an_intro(client, db, professions)
    as_caller.patch(f"/api/introductions/{intro.id}", json={"outcome": "buyer_proceeded"})
    db.refresh(intro)
    token = intro.review_token

    # The outcome is corrected afterwards — the token must stop working.
    as_caller.patch(f"/api/introductions/{intro.id}", json={"outcome": "no_response"})
    assert client.get(f"/api/public/reviews/{token}").status_code == 404


def test_a_verified_review_can_only_be_left_once(as_caller, client, db, professions):
    intro = _an_intro(client, db, professions)
    as_caller.patch(f"/api/introductions/{intro.id}",
                    json={"status": "closed", "outcome": "buyer_proceeded"})
    db.refresh(intro)

    first = client.post(f"/api/public/reviews/{intro.review_token}",
                        json={"stars": 5, "text": "Excellent throughout."})
    assert first.status_code == 201, first.text
    second = client.post(f"/api/public/reviews/{intro.review_token}",
                         json={"stars": 1, "text": "Changed my mind."})
    assert second.status_code == 409
    assert db.query(Review).count() == 1


def test_a_verified_review_is_labelled_differently_from_a_google_one(
    as_caller, client, db, professions
):
    """The distinction is the whole point: clause 4 is enforceable for reviews
    Vilaow controls, and not for content Google owns."""
    intro = _an_intro(client, db, professions)
    db.add(Review(professional_id=intro.professional_id, author="Old G.",
                  stars=4, text="From Google.", source="via Google"))
    db.commit()

    as_caller.patch(f"/api/introductions/{intro.id}",
                    json={"status": "closed", "outcome": "buyer_proceeded"})
    db.refresh(intro)
    client.post(f"/api/public/reviews/{intro.review_token}", json={"stars": 5, "text": "Great."})

    reviews = client.get("/api/public/professionals/kostas").json()["reviews"]
    by_kind = {r["kind"]: r for r in reviews}
    assert by_kind["vilaow_verified"]["verified"] is True
    assert by_kind["google"]["verified"] is False
    # And a real person's full name is not published forever.
    assert by_kind["vilaow_verified"]["author"] == "Sarah M."


def test_the_review_request_waits_before_asking(as_caller, client, db, professions, outbox):
    """Long enough that the work has actually happened."""
    intro = _an_intro(client, db, professions)
    as_caller.patch(f"/api/introductions/{intro.id}",
                    json={"status": "closed", "outcome": "buyer_proceeded"})

    assert as_caller.post("/api/introductions/send-review-requests").json()["sent"] == 0

    db.refresh(intro)
    intro.closed_at = datetime.now(timezone.utc) - timedelta(days=4)
    db.commit()

    assert as_caller.post("/api/introductions/send-review-requests").json()["sent"] == 1
    assert any("How did it go" in m.subject for m in outbox.outbox)

    # And never twice.
    assert as_caller.post("/api/introductions/send-review-requests").json()["sent"] == 0

    # Recorded on the professional's timeline, like the other two emails.
    assert "review_requested" in _events(db, intro.professional_id)
