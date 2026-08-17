"""The public surface, and what a stranger can do to it.

Every case here was raised by an agent review and confirmed against the code.
They share a shape: an endpoint anyone on the internet can reach, doing
something a real user would never do.

  * The login form had no rate limit of any kind — unlimited parallel guesses,
    and each one costs a bcrypt hash, so the same requests exhaust a small
    instance's CPU.
  * The public callback form had no limit, no honeypot and no field lengths.
    The introduction form next to it has all three.
  * Rate limiting keyed on the connection's peer address, which behind Render's
    load balancer is always the proxy — so the per-caller cap was a single
    shared bucket for the whole internet, cheap for one person to exhaust.
  * User text went unescaped into the HTML of Vilaow-branded email.
  * A bucket outage escaped the storage adapter as a raw 500.
"""
from __future__ import annotations

import pytest

from app.api.clients import client_ip
from app.api.throttle import SlidingWindow, lead_submissions, login_attempts
from app.config import Settings
from app.models import Lead


# Cleared before every test by an autouse fixture in conftest.py — the
# throttles are process-wide, and `as_owner`/`as_caller` sign in constantly.


def _lead(**kw) -> dict:
    base = {"buyer_name": "Sarah Mitchell", "buyer_phone": "+44 7700 900123",
            "buyer_email": "sarah@example.com", "message": "Buying near Chania."}
    base.update(kw)
    return base


# ── login ───────────────────────────────────────────────────────────────────
def test_password_guessing_is_cut_off(client, owner):
    wrong = {"email": owner.email, "password": "not-the-password"}
    seen = [client.post("/api/auth/login", json=wrong).status_code for _ in range(8)]

    assert 401 in seen, "the first attempts should be ordinary failures"
    assert 429 in seen, (
        "unlimited guesses were accepted on an internet-facing endpoint, and "
        "each one costs a bcrypt hash"
    )


def test_the_limit_follows_the_account_not_only_the_address(client, owner):
    """A distributed attempt on one known email is the case a per-address
    limit alone does not cover."""
    for i in range(6):
        client.post("/api/auth/login",
                    json={"email": owner.email, "password": f"guess-{i}"},
                    headers={"x-forwarded-for": f"93.184.216.{i}"})

    blocked = client.post("/api/auth/login",
                          json={"email": owner.email, "password": "another"},
                          headers={"x-forwarded-for": "93.184.216.99"})
    assert blocked.status_code == 429


def test_signing_in_clears_the_count(client, owner):
    """One mistyped password must not follow someone who then gets it right."""
    client.post("/api/auth/login", json={"email": owner.email, "password": "wrong"})
    assert client.post("/api/auth/login",
                       json={"email": owner.email, "password": "owner-pw"}).status_code == 200
    # Still able to sign in again immediately.
    assert client.post("/api/auth/login",
                       json={"email": owner.email, "password": "owner-pw"}).status_code == 200


# ── the public callback form ────────────────────────────────────────────────
def test_the_callback_form_cannot_be_used_to_flood_the_worklist(client, db):
    seen = [client.post("/api/leads", json=_lead()).status_code for _ in range(8)]
    assert 201 in seen
    assert 429 in seen, "anyone with a script could fill the caller worklist"


def test_the_honeypot_is_accepted_and_discarded(client, db):
    """Answer as success, store nothing — whatever filled it learns nothing."""
    r = client.post("/api/leads", json=_lead(website="http://spam.example"))
    assert r.status_code == 201
    assert db.query(Lead).count() == 0


@pytest.mark.parametrize("bad, why", [
    ({"buyer_name": "x" * 500}, "an oversized name"),
    ({"buyer_phone": "9" * 500}, "an oversized phone"),
    ({"message": "x" * 5000}, "an oversized message"),
    ({"buyer_email": "not-an-email"}, "a malformed address"),
    ({"buyer_name": ""}, "an empty name"),
])
def test_a_malformed_submission_is_a_clear_refusal_not_a_500(bad, why, client):
    """A stranger must not be able to make this endpoint throw."""
    assert client.post("/api/leads", json=_lead(**bad)).status_code == 422, why


def test_a_professional_id_pointing_at_nothing_keeps_the_enquiry(client, db):
    """It was a foreign key error and an unhandled 500. The enquiry is the part
    the buyer cares about, so it survives without the broken link."""
    r = client.post("/api/leads", json=_lead(professional_id=999999))
    assert r.status_code == 201, r.text
    assert db.query(Lead).one().professional_id is None


# ── who the limiter thinks you are ──────────────────────────────────────────
class _Request:
    def __init__(self, peer: str | None, forwarded: str | None = None):
        self.client = type("C", (), {"host": peer})() if peer else None
        self.headers = {"x-forwarded-for": forwarded} if forwarded else {}


def _settings(**kw) -> Settings:
    base = dict(database_url="postgresql+psycopg://localhost/vilaow",
                secret_key="s", _env_file=None)
    base.update(kw)
    return Settings(**base)


def test_in_production_the_caller_is_read_from_the_proxy_header():
    """Behind Render's load balancer the peer is always the balancer, so every
    visitor shared one bucket and one person could lock the form for everyone."""
    # Genuinely public addresses: Python classifies the RFC 5737 documentation
    # ranges (203.0.113.0/24 and friends) as private, and the check below is
    # right to skip those — they never carry real traffic.
    request = _Request("10.0.0.7", forwarded="93.184.216.34, 10.0.0.7")
    assert client_ip(request, _settings(environment="production")) == "93.184.216.34"


def test_in_development_the_peer_is_already_the_real_caller():
    """Nothing is trusted that does not need to be — the header is attacker
    controlled, and locally there is no proxy to set it."""
    request = _Request("127.0.0.1", forwarded="93.184.216.34")
    assert client_ip(request, _settings(environment="development")) == "127.0.0.1"


@pytest.mark.parametrize("forwarded", ["10.0.0.1", "192.168.1.1", "not-an-ip", ""])
def test_an_unusable_forwarded_address_is_skipped(forwarded):
    """A private or junk value must not become the key — it would rebuild the
    shared bucket under a different name."""
    request = _Request("8.8.4.4", forwarded=forwarded)
    assert client_ip(request, _settings(environment="production")) == "8.8.4.4"


def test_an_unknown_caller_is_not_a_shared_bucket():
    assert client_ip(_Request(None), _settings(environment="production")) is None
    assert SlidingWindow(limit=1, window=60).check(None) is True
    # And again — None never counts against itself.
    assert SlidingWindow(limit=1, window=60).check(None) is True


def test_the_window_actually_counts():
    window = SlidingWindow(limit=2, window=60)
    assert [window.check("k") for _ in range(4)] == [True, True, False, False]
    window.clear("k")
    assert window.check("k") is True


# ── email ───────────────────────────────────────────────────────────────────
def test_a_buyers_text_cannot_inject_markup_into_a_vilaow_email():
    """Unescaped, `</table><p>Confirm your bank details at …` closed the layout
    and continued in Vilaow's own voice, inside an email Vilaow sent."""
    from app.adapters.email import templates

    message = templates.introduction_to_professional(
        to="pro@example.com",
        name="Kostas",
        buyer_name='</b><script>alert(1)</script><b>',
        buyer_email="sarah@example.com",
        buyer_phone='"><a href="http://evil.example">click</a>',
        message="</table><p>Please confirm your bank details</p>",
    )

    assert "<script>" not in message.html
    assert "&lt;script&gt;" in message.html
    assert '<a href="http://evil.example"' not in message.html
    assert "</table><p>Please confirm" not in message.html
    # The plain-text half is not markup and must stay readable.
    assert "<script>" in message.text


def test_escaping_did_not_break_ordinary_names():
    from app.adapters.email import templates

    message = templates.introduction_to_professional(
        to="pro@example.com", name="Γιώργος", buyer_name="Sarah O'Brien",
        buyer_email="sarah@example.com", buyer_phone="+44 7700 900123", message=None,
    )
    assert "Sarah O" in message.html
    assert "sarah@example.com" in message.html


# ── storage ─────────────────────────────────────────────────────────────────
def test_a_bucket_outage_is_a_storage_failure_not_a_raw_500():
    """assets.py turns StorageFailure into a 404 and a failed delete into a
    completed erasure. An SDK exception sails straight past both."""
    from app.adapters.storage.firebase import _as_storage_failure
    from app.domain.errors import StorageFailure

    for raised in (ConnectionError("bucket unreachable"), OSError("socket"),
                   RuntimeError("credentials revoked")):
        with pytest.raises(StorageFailure):
            with _as_storage_failure("read"):
                raise raised


def test_a_storage_failure_passes_through_unchanged():
    from app.adapters.storage.firebase import _as_storage_failure
    from app.domain.errors import StorageFailure

    with pytest.raises(StorageFailure, match="file not found"):
        with _as_storage_failure("read"):
            raise StorageFailure("file not found")
