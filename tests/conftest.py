"""Test fixtures.

Against a real Postgres, not SQLite. The schema uses Postgres enums, ARRAY and
JSONB, and the overdue-lead query depends on timezone-aware comparison — none
of which SQLite would exercise faithfully. A test suite that passes on a
database you do not deploy is worth very little.
"""
from __future__ import annotations

import os
import re
import sys

# These fixtures run DROP SCHEMA and TRUNCATE. Two guards, because getting this
# wrong destroys real data and the first version of this file did exactly that.
#
# setdefault() was the bug: it leaves an already-exported DATABASE_URL alone, so
# running the suite in a shell where DATABASE_URL pointed at the development
# database dropped that schema instead of the test one. Pointed at production it
# would have dropped production.
#
# 1. Force the test database rather than defaulting to it.
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://localhost/vilaow_test"
)
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["SECRET_KEY"] = "test-secret"
os.environ["ENVIRONMENT"] = "test"

# 2. Refuse to run against anything not obviously a throwaway, even if someone
#    sets TEST_DATABASE_URL deliberately. A destructive suite should not be one
#    typo away from a real database.
_name = TEST_DATABASE_URL.rsplit("/", 1)[-1].split("?")[0]
if not (_name.endswith("_test") or _name.startswith("test_")):
    sys.exit(
        f"Refusing to run: the test suite drops and truncates every table, and "
        f"'{_name}' is not named like a test database. Name it *_test or test_*."
    )
if any(h in TEST_DATABASE_URL for h in ("render.com", "amazonaws.com", "supabase.co")):
    sys.exit("Refusing to run the destructive test suite against a hosted database.")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db import SessionLocal, engine
from app.models import Base, Profession, Role, Staff
from app.security import hash_password
from app.seed import PROFESSIONS


@pytest.fixture(scope="session", autouse=True)
def schema():
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def outbox():
    """The email the app sent, without a mail server anywhere near it.

    This used to monkeypatch a module-level `send`, because there was no seam
    to inject anything into. There is now: `EmailSender` is a port, and the
    app asks for one through `get_email_sender`. Overriding that dependency
    hands the whole application a capturing implementation on equal footing
    with SMTP.

    It matters beyond tidiness. Settings read .env, and .env carries live Gmail
    credentials, so without this the suite opens real TLS connections and tries
    to deliver to whatever address a fixture invented.
    """
    from app.adapters.email.senders import InMemoryEmailSender
    from app.api.deps import get_email_sender
    from app.main import app

    sender = InMemoryEmailSender()
    app.dependency_overrides[get_email_sender] = lambda: sender
    yield sender
    app.dependency_overrides.pop(get_email_sender, None)


@pytest.fixture(autouse=True)
def _never_send_real_email(outbox):
    """Applied to every test, not only the ones that read the outbox."""
    return outbox


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    """The throttles are process-wide, so they leak between tests.

    Not a nicety: `as_owner` and `as_caller` sign in on almost every test, so
    without this the sixth test in a minute meets the login limiter and the
    fixture itself starts failing. Clearing per test keeps the limiter real —
    tests/test_hardening.py exercises it deliberately — without letting one
    test's attempts count against the next.
    """
    from app.api.throttle import lead_submissions, login_attempts

    for window in (login_attempts, lead_submissions):
        window._hits.clear()
    yield


@pytest.fixture(autouse=True)
def clean_tables(schema):
    """Every test starts from an empty database.

    TRUNCATE ... RESTART IDENTITY CASCADE rather than deleting rows, so ids do
    not drift between tests and make failures hard to read.
    """
    yield
    with engine.begin() as conn:
        names = ",".join(f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables))
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s


@pytest.fixture
def professions(db):
    rows = [
        Profession(key=k, label=l, plural=p, hint=h, position=i)
        for i, (k, l, p, h) in enumerate(PROFESSIONS)
    ]
    db.add_all(rows)
    db.commit()
    return {r.key: r.id for r in rows}


@pytest.fixture
def owner(db):
    s = Staff(name="Owner", email="owner@vilaow.com",
              password_hash=hash_password("owner-pw"), role=Role.owner)
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def caller(db):
    s = Staff(name="Caller", email="caller@vilaow.com",
              password_hash=hash_password("caller-pw"), role=Role.caller)
    db.add(s)
    db.commit()
    return s


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture
def as_owner(client, owner):
    r = client.post("/api/auth/login", json={"email": owner.email, "password": "owner-pw"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def as_caller(client, caller):
    r = client.post("/api/auth/login", json={"email": caller.email, "password": "caller-pw"})
    assert r.status_code == 200, r.text
    return client


@pytest.fixture
def codes(outbox):
    """The confirmation codes the app actually emailed.

    The code is stored only as a bcrypt hash, so a test cannot read it back
    from the database — which is the point. Reading it out of the sent message
    is the honest way, and it asserts the code really was delivered rather than
    that a patched function was called.
    """

    class Codes:
        def __getitem__(self, index: int) -> str:
            sent = [m for m in outbox.outbox if "confirmation code" in m.subject]
            return re.search(r"\b(\d{6})\b", sent[index].subject).group(1)

        def __bool__(self) -> bool:
            return any("confirmation code" in m.subject for m in outbox.outbox)

    return Codes()
