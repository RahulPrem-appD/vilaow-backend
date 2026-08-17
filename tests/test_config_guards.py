"""Configurations the process must refuse to start under.

Both of these had already happened. The repo's own `.env` carried
`ENVIRONMENT=development` next to the Render production DSN and
`PUBLIC_SITE_URL=http://localhost:3000`, which means every local run wrote to
real records, and any signing link it emailed to a real Greek professional
pointed at a server on this laptop.

Nothing about that is visible while you work — the app starts, the data looks
plausible, and the damage is discovered later by someone else. It is cheap to
make the process refuse, so it does.
"""
from __future__ import annotations

import pytest

from app.config import Settings

RENDER = ("postgresql+psycopg://u:p@dpg-abc-a.oregon-postgres.render.com/vilaow")
LOCAL = "postgresql+psycopg://localhost/vilaow"


def _settings(**kw) -> Settings:
    base = dict(database_url=LOCAL, environment="development",
                public_site_url="http://localhost:3000",
                secret_key="test-secret", _env_file=None)
    base.update(kw)
    return Settings(**base)


def _production(**kw) -> Settings:
    """A production configuration that is complete, so each test can break
    exactly one thing and see that one thing reported."""
    base = dict(database_url=RENDER, environment="production",
                secret_key="a-real-secret", public_site_url="https://vilaow.com",
                smtp_user="vilaow@example.com", smtp_password="app-password",
                firebase_bucket="vilaow.firebasestorage.app")
    base.update(kw)
    return _settings(**base)


def test_a_development_run_will_not_start_against_a_hosted_database():
    with pytest.raises(RuntimeError, match="hosted database"):
        _settings(database_url=RENDER).validate_for_production()


def test_a_local_database_is_fine():
    _settings().validate_for_production()


def test_production_is_expected_to_use_a_hosted_database():
    _production().validate_for_production()


# ── a production deploy that cannot do its job must not start ───────────────
# render.yaml set four environment variables; the code read eleven. A rebuild
# from the blueprint produced an API that emailed localhost links, could not
# send mail and could not store a file — each failing per request, deep inside
# a flow, while /health stayed green and the service looked live.
@pytest.mark.parametrize("broken, expected", [
    ({"public_site_url": "http://localhost:3000"}, "PUBLIC_SITE_URL"),
    ({"public_site_url": "http://127.0.0.1:3000"}, "PUBLIC_SITE_URL"),
    ({"smtp_user": "", "smtp_password": ""}, "SMTP_USER"),
    ({"smtp_password": ""}, "SMTP_USER"),
    ({"firebase_bucket": ""}, "FIREBASE_BUCKET"),
])
def test_production_refuses_to_start_without_what_it_needs(broken, expected):
    with pytest.raises(RuntimeError, match=expected):
        _production(**broken).validate_for_production()


def test_every_missing_setting_is_named_at_once():
    """One restart should tell you everything that is wrong, not the first
    thing — a deploy loop that reveals one variable at a time is how this stays
    half-configured."""
    with pytest.raises(RuntimeError) as caught:
        _production(public_site_url="http://localhost:3000", smtp_user="",
                    firebase_bucket="").validate_for_production()
    message = str(caught.value)
    assert "PUBLIC_SITE_URL" in message
    assert "SMTP_USER" in message
    assert "FIREBASE_BUCKET" in message


def test_development_is_not_held_to_the_production_checklist():
    """Local work has no SMTP, no bucket and a localhost site URL by design."""
    _settings(smtp_user="", firebase_bucket="").validate_for_production()


@pytest.mark.parametrize("url, local", [
    ("http://localhost:3000", True),
    ("http://127.0.0.1:3000", True),
    ("http://0.0.0.0:3000", True),
    ("http://vilaow.local", True),
    ("https://vilaow.com", False),
    ("https://valow-mono.vercel.app", False),
])
def test_a_site_url_is_recognised_as_local_or_not(url, local):
    assert _settings(public_site_url=url).site_url_is_local is local


def test_the_override_lets_a_deliberate_one_off_through(monkeypatch):
    monkeypatch.setenv("VILAOW_ALLOW_HOSTED_DB", "1")
    _settings(database_url=RENDER,
              public_site_url="https://vilaow.com").validate_for_production()


def test_the_override_still_refuses_to_email_localhost_links(monkeypatch):
    """The dangerous half of the combination. Writing to production knowingly
    is a choice; sending a real professional a link to your laptop is not."""
    monkeypatch.setenv("VILAOW_ALLOW_HOSTED_DB", "1")
    with pytest.raises(RuntimeError, match="PUBLIC_SITE_URL"):
        _settings(database_url=RENDER,
                  public_site_url="http://localhost:3000").validate_for_production()


@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@db.abcdefg.supabase.co:5432/postgres",
    "postgresql://u:p@ep-cool-name.eu-central-1.aws.neon.tech/vilaow",
    "postgresql://u:p@vilaow.abc123.eu-west-1.rds.amazonaws.com/vilaow",
    "postgres://u:p@dpg-xyz-a.frankfurt-postgres.render.com/vilaow",
])
def test_the_usual_managed_providers_are_recognised(dsn):
    assert _settings(database_url=dsn).database_is_hosted


@pytest.mark.parametrize("dsn", [
    LOCAL,
    "postgresql+psycopg://127.0.0.1:5432/vilaow",
    "postgresql+psycopg://user:pw@localhost/vilaow_test",
])
def test_a_local_dsn_is_not_mistaken_for_a_hosted_one(dsn):
    """A guard that fires on everything gets an override exported in a shell
    profile, and then it is not a guard."""
    assert not _settings(database_url=dsn).database_is_hosted


def test_the_published_dev_secret_still_cannot_reach_production():
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        _settings(database_url=RENDER, environment="production",
                  secret_key="dev-only-not-for-production",
                  public_site_url="https://vilaow.com").validate_for_production()
