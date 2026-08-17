"""Settings, read from the environment.

Nothing here has a usable default for a secret. A missing SECRET_KEY in
production must stop the process, not silently fall back to a shared constant
that ends up signing real session cookies.
"""
import os
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# Substrings that mean "this database is not on your laptop". Not exhaustive,
# and it does not need to be: it is a seatbelt for the providers this project
# actually uses, not a security boundary.
_HOSTED_DATABASE_HOSTS = (
    "render.com", "amazonaws.com", "supabase.co", "neon.tech",
    "azure.com", "digitalocean.com", "heroku", "gcp.",
)
_OVERRIDE = "VILAOW_ALLOW_HOSTED_DB"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(
        default="postgresql+psycopg://localhost/vilaow",
        description="Postgres DSN. Render supplies postgres://, which SQLAlchemy "
                    "needs as postgresql+psycopg:// — normalised below.",
    )
    secret_key: str = Field(default="dev-only-not-for-production")
    environment: str = Field(default="development")

    session_cookie: str = "vilaow_session"
    session_max_age: int = 60 * 60 * 12          # a working day
    agreement_ttl_days: int = 30                  # how long a signing link lives
    terms_version: str = "2026-01"                # his eight clauses, as published

    cors_origins: str = "http://localhost:3000"

    # Email. The agreement link is the only thing this system sends to someone
    # outside the company, and it is the step the whole pipeline depends on:
    # a professional who never receives it never signs.
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587                       # STARTTLS
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""                        # defaults to smtp_user
    smtp_from_name: str = "Vilaow"
    # Where a signing link points. The API and the site are different hosts.
    public_site_url: str = "http://localhost:3000"

    # Firebase Storage, for profile photos and licence documents. Unset in
    # development, where uploads fall back to local disk; app/storage.py
    # refuses that fallback in production, because Render's filesystem is
    # ephemeral and the files would disappear on the next deploy.
    #
    # firebase_credentials_file is a path to a service account JSON key — a
    # path, never the key itself, so the secret is not read out of a variable
    # that ends up in logs or a process listing.
    firebase_bucket: str = ""
    firebase_credentials_file: str = ""

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def mail_from(self) -> str:
        return self.smtp_from or self.smtp_user

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        # Render and Heroku hand out postgres://; SQLAlchemy 2 wants a driver.
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+psycopg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+psycopg://", 1)
        return url

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def site_url_is_local(self) -> bool:
        """A site URL that only resolves on the machine that set it."""
        host = self.public_site_url.split("//")[-1].split("/")[0].split(":")[0].lower()
        return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1", ""} or host.endswith(".local")

    @property
    def database_is_hosted(self) -> bool:
        """A managed database somewhere, as opposed to one on this machine."""
        host = self.database_url.split("@")[-1].split("/")[0].lower()
        return any(h in host for h in _HOSTED_DATABASE_HOSTS)

    def validate_for_production(self) -> None:
        if self.is_production and self.secret_key == "dev-only-not-for-production":
            raise RuntimeError(
                "SECRET_KEY is unset in production. Refusing to start: the default "
                "would sign session cookies with a value published in the repo."
            )

        # A development run pointed at the production database is not a
        # theoretical mistake; this .env shipped that way. Everything a local
        # run does — issuing agreements, publishing profiles, deleting files —
        # lands on real records, and every signing link it emails to a real
        # professional points at http://localhost:3000, which is a dead link on
        # their machine. Both halves of that are visible from here, so neither
        # has to depend on anyone remembering.
        if self.database_is_hosted and not self.is_production:
            if os.environ.get(_OVERRIDE) != "1":
                raise RuntimeError(
                    f"Refusing to start: ENVIRONMENT is '{self.environment}' but "
                    f"DATABASE_URL points at a hosted database. A local run would "
                    f"write to real records. Point DATABASE_URL at a local "
                    f"database, or set {_OVERRIDE}=1 for a deliberate one-off "
                    f"against production."
                )
            if self.site_url_is_local:
                raise RuntimeError(
                    f"Refusing to start: {_OVERRIDE} is set, but PUBLIC_SITE_URL "
                    f"is {self.public_site_url}. Agreement emails to real "
                    f"professionals would carry a link to your own machine."
                )

        if not self.is_production:
            return

        # Everything below is a setting the code reads that render.yaml did not
        # set. Each one fails per *request*, deep inside a flow, while /health
        # stays green — so a blueprint rebuild produced an API that looked
        # healthy and could not do its job. Failing at boot makes a missing
        # variable a deploy that does not go live, rather than a professional
        # who never receives their link.
        missing: list[str] = []

        if self.site_url_is_local:
            missing.append(
                f"PUBLIC_SITE_URL is {self.public_site_url!r} — every signing and "
                f"review link emailed to a real person would point at the server "
                f"itself"
            )
        if not self.email_configured:
            missing.append(
                "SMTP_USER/SMTP_PASSWORD are unset — the agreement link is the "
                "one thing this system sends outside the company, and a "
                "professional who never receives it never signs"
            )
        if not self.firebase_bucket:
            missing.append(
                "FIREBASE_BUCKET is unset — uploads would fall back to a "
                "container filesystem that Render discards on the next deploy, "
                "taking every photo and licence scan with it"
            )

        if missing:
            raise RuntimeError(
                "Refusing to start in production with an incomplete configuration:\n  - "
                + "\n  - ".join(missing)
                + "\nSee backend/.env.example and render.yaml."
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
